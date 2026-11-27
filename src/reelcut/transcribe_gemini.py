"""Gemini-backed voiceover transcription: clean script + exact timings.

Two passes, because they solve different problems:

1. **Text** — the whole recording goes to Gemini 3.5 Flash with the voiceover
   editor system instruction (final take only, drop stumbles and side chatter,
   stitch pickups, keep the dialect). This is where quality comes from.
2. **Time** — Silero VAD (local, free) finds speech windows, each window is
   transcribed on its own, and the clean script lines are aligned onto those
   windows. Gemini's own timestamps drift (a 121 s file produced a
   157-201 s segment), so seconds always come from the audio, never the model.

Public API:
    transcript = transcribe_voiceover(audio_path, model=..., language="ar")
    # {"script", "segments": [{start,end,text}], "dropped": [...],
    #  "speech_seconds", "windows", "model"}

Creators get segments that are safe to cut on: every boundary sits in silence.
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

API = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = os.environ.get("GEMINI_TRANSCRIBE_MODEL", "gemini-3.5-flash")
MERGE_GAP = 0.45        # speech windows closer than this are one take
MAX_SPAN = 3            # a script line may span at most 3 windows
MIN_SIMILARITY = 0.45   # token overlap needed to accept an alignment

SYSTEM_INSTRUCTION = """You are an expert voiceover editor and script transcriber. \
Your job is to listen to raw voiceover recording sessions (which contain multiple takes, \
stumbles, false starts, and side conversations) and extract ONLY the final, clean, intended script.

Follow these rules strictly:
1. Select the final/best take: When a line is repeated multiple times, keep only the most complete, fluent, and final version.
2. Remove errors: Discard all false starts, stutters, aborted sentences, breaths, and throat clearings.
3. Ignore off-script chatter: Exclude any side comments, acknowledgments, or conversations with others in the room (e.g., "ماشي", "ثانية واحدة", "أقولك", etc.).
4. Stitch pickups seamlessly: If the speaker repeats the last few words of a previous line as a lead-in (punch-in) to finish the sentence, merge them cleanly without duplicating the overlapping words.
5. Preserve exact wording & dialect: Transcribe verbatim in the exact spoken dialect (e.g., keep Egyptian Arabic as spoken; do not translate or convert to Modern Standard Arabic). Keep English words or phrases in English if spoken that way.
6. Output format: Return ONLY the clean, punctuated final script. Do not include timestamps, take labels, notes, or introductory/closing text.

Additionally return the pickups you merged so the caller can verify rule 4:
{"script": "<the clean final script, punctuation included>",
 "pickups": [{"lead": "<repeated words>", "merged_into": "<line>"}]}
Keep "script" free of timestamps and labels; "pickups" may be empty."""

USER_PROMPT = "Extract the final intended script from this voiceover recording."

WINDOW_PROMPT = (
    "Transcribe this Arabic (Egyptian) audio segment verbatim, exactly as spoken, "
    "including false starts. Return JSON only: {\"text\": \"...\"}. "
    "No translation, no corrections, no commentary.")


# --------------------------------------------------------------------- helpers
def _ffmpeg() -> str:
    from .media import get_ffmpeg_exe

    return get_ffmpeg_exe()


def _pcm16(audio: Path) -> Path:
    """Any input -> 16 kHz mono wav (what the API and VAD both want)."""
    audio = Path(audio)
    if audio.suffix.lower() == ".wav":
        return audio
    target = Path(tempfile.mkdtemp(prefix="vo_")) / "audio16k.wav"
    subprocess.run([_ffmpeg(), "-y", "-v", "error", "-i", str(audio),
                    "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                    str(target)], check=True, capture_output=True)
    return target


def load_key() -> str:
    try:
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parents[2] / ".env",
                    override=False)
    except Exception:  # noqa: BLE001
        pass
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Add it to .env or the environment; "
            "Gemini transcription and B-roll images both need it.")
    return key


def _post(model: str, body: dict, key: str, timeout: int = 600) -> dict:
    import httpx

    with httpx.Client(timeout=timeout) as client:
        r = client.post(f"{API}/models/{model}:generateContent",
                        params={"key": key}, json=body)
        if r.status_code >= 400:
            raise RuntimeError(f"Gemini HTTP {r.status_code}: {r.text[:500]}")
        return r.json()


def _json_part(resp: dict) -> str:
    try:
        return resp["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as exc:  # noqa: BLE001
        raise RuntimeError(f"unexpected Gemini response: {str(resp)[:400]}") from exc


# ---------------------------------------------------------------- pass A (text)
def clean_script(audio: Path, model: str = DEFAULT_MODEL,
                 key: str | None = None) -> dict[str, Any]:
    """Whole-recording pass: final script only, per the system instruction."""
    key = key or load_key()
    data = _pcm16(audio).read_bytes()
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
        "contents": [{"role": "user", "parts": [
            {"text": USER_PROMPT},
            {"inline_data": {"mime_type": "audio/wav",
                             "data": base64.b64encode(data).decode()}}]}],
        "generationConfig": {"temperature": 0.1,
                             "response_mime_type": "application/json",
                             "maxOutputTokens": 8192},
    }
    parsed = json.loads(_json_part(_post(model, body, key)))
    script = " ".join(str(parsed.get("script", "")).split())
    return {"script": script,
            "pickups": parsed.get("pickups") or [],
            "model": model}


# --------------------------------------------------------------- pass B (time)
def speech_windows(wav: Path) -> list[dict[str, float]]:
    """VAD windows in seconds (local, free)."""
    from .vad import detect_speech_segments

    rows = [{"start": round(s.start, 2), "end": round(s.end, 2)}
            for s in detect_speech_segments(wav)]
    merged: list[dict[str, float]] = []
    for r in rows:
        if merged and r["start"] - merged[-1]["end"] <= MERGE_GAP:
            merged[-1]["end"] = r["end"]
        else:
            merged.append(dict(r))
    return merged


def _window_text(wav: Path, start: float, end: float, model: str,
                 key: str) -> str:
    tmp = Path(tempfile.mkdtemp(prefix="win_")) / "w.wav"
    subprocess.run([_ffmpeg(), "-y", "-v", "error", "-ss", f"{max(0, start - 0.08):.3f}",
                    "-to", f"{end + 0.08:.3f}", "-i", str(wav), "-c:a", "pcm_s16le",
                    str(tmp)], check=True, capture_output=True)
    body = {"contents": [{"role": "user", "parts": [
        {"text": WINDOW_PROMPT},
        {"inline_data": {"mime_type": "audio/wav",
                         "data": base64.b64encode(tmp.read_bytes()).decode()}}]}],
        "generationConfig": {"temperature": 0.0,
                             "response_mime_type": "application/json",
                             "maxOutputTokens": 512}}
    try:
        return " ".join(str(json.loads(_json_part(_post(model, body, key)))
                            .get("text", "")).split())
    except Exception:  # noqa: BLE001 - one bad window must not kill the run
        return ""


def normalize_ar(text: str) -> str:
    """Compare dialect spellings without diacritics/alef variations."""
    text = unicodedata.normalize("NFKC", text or "")
    text = re.sub(r"[\u064B-\u065F\u0670\u0640]", "", text)      # diacritics/tatweel
    text = re.sub("[إأآٱ]", "ا", text).replace("ى", "ي").replace("ة", "ه")
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.lower().split())


def split_lines(script: str) -> list[str]:
    """Sentence-ish lines from the clean script (keeps punctuation)."""
    parts = re.split(r"(?<=[.!?؟])\s+|\n+", script.strip())
    return [p.strip() for p in parts if p and p.strip()]


def _tokens(text: str) -> set[str]:
    return set(normalize_ar(text).split())


def align(lines: list[str], windows: list[dict[str, Any]]
          ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Monotonic line -> window-span alignment by token overlap.

    A sentence split by a short pause may span consecutive windows; a window
    that matches nothing is chatter and gets dropped (reported, not hidden).
    """
    out: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    cursor = 0
    used: set[int] = set()
    for line in lines:
        want = _tokens(line)
        if not want:
            continue
        best: tuple[float, int, int] | None = None
        for i in range(cursor, len(windows)):
            for span in range(1, MAX_SPAN + 1):
                if i + span > len(windows):
                    break
                have: set[str] = set()
                for j in range(i, i + span):
                    have |= _tokens(windows[j]["text"])
                score = len(want & have) / max(1, len(want))
                # ties prefer the TIGHTER span, so side chatter is not swallowed
                if best is None or score > best[0] + 1e-9 or (
                        abs(score - best[0]) <= 1e-9 and span < best[2]):
                    best = (score, i, span)
        if best and best[0] >= MIN_SIMILARITY:
            score, i, span = best
            j = i + span
            out.append({"start": windows[i]["start"], "end": windows[j - 1]["end"],
                        "text": line, "score": round(score, 2)})
            used.update(range(i, j))
            cursor = j
        else:
            out.append({"start": None, "end": None, "text": line,
                        "score": round(best[0], 2) if best else 0.0,
                        "unmatched": True})
    for idx, w in enumerate(windows):
        if idx not in used and w.get("text"):
            dropped.append({"start": w["start"], "end": w["end"],
                            "text": w["text"], "reason": "not in the clean script"})
    return out, dropped


def transcribe_voiceover(audio: str | Path, model: str = DEFAULT_MODEL,
                         language: str = "ar", workers: int = 4,
                         log=print) -> dict[str, Any]:
    """Full pipeline: clean script + VAD windows + exact-second segments."""
    key = load_key()
    wav = _pcm16(Path(audio))
    t0 = time.time()
    log(f"[transcribe] clean-script pass ({model})")
    text = clean_script(wav, model=model, key=key)
    windows = speech_windows(wav)
    log(f"[transcribe] {len(windows)} speech windows; transcribing each")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        texts = list(pool.map(lambda w: _window_text(wav, w["start"], w["end"],
                                                     model, key), windows))
    for w, t in zip(windows, texts):
        w["text"] = t
    lines = split_lines(text["script"])
    segments, dropped = align(lines, windows)
    speech = round(sum(w["end"] - w["start"] for w in windows), 2)
    return {
        "language": language,
        "model": model,
        "script": text["script"],
        "pickups": text["pickups"],
        "segments": segments,
        "dropped": dropped,
        "windows": len(windows),
        "speech_seconds": speech,
        "seconds": round(time.time() - t0, 1),
    }


def write_transcript(project_dir: str | Path, result: dict[str, Any]) -> Path:
    target = Path(project_dir) / "work" / "gemini_transcript.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    return target


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python -m reelcut.transcribe_gemini <audio>")
        raise SystemExit(2)
    res = transcribe_voiceover(sys.argv[1])
    for seg in res["segments"]:
        when = (f"{seg['start']:6.2f}-{seg['end']:6.2f}"
                if seg.get("start") is not None else "  ?  -  ? ")
        print(f"[{when}] {seg['text']}")
    print(f"\nspeech {res['speech_seconds']}s, {res['windows']} windows, "
          f"{len(res['dropped'])} dropped")
