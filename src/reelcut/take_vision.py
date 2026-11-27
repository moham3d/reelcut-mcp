"""Visual take review: is the creator presenting, or still preparing?

Audio alignment answers "does the take say the right words". It cannot answer
"is this the take we want in the product": creators check the phone, look at
notes, talk to someone off camera, or reset the frame while still speaking the
line. A cut built from audio alone therefore keeps preparation moments - the
difference between an audio-only cut and Antigravity's better one.

    review_take(video, start, end, samples=3)
    -> {"usable", "ratio", "samples", "trim_start", "trim_end", "reason"}
    review_segments(video, [{"start","end","text"}]) -> verdicts

Each sampled frame goes to Gemini vision with a one-line contract (person |
eye contact | usable | reason), which is far more reliable than JSON mode on
this model. Suggested trims remove leading/trailing preparation frames without
touching the spoken words.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

API = "https://generativelanguage.googleapis.com/v1beta"
MODEL = os.environ.get("GEMINI_VISION_MODEL", "gemini-2.5-flash")
PROMPT = """You review one frame of a talking-head take for a short video.

Reply with EXACTLY ONE line, four fields separated by "|" and nothing else:
person|eye_contact|usable|reason

person is one of: camera, phone, notes, away, other_person, none
- camera = facing the lens, eyes toward it, free hands, presenting
- phone/notes = holding or looking at a phone or notes
- away = turned away, eyes off to the side or down, walking, adjusting
- other_person = addressing or listening to somebody off camera
- none = no person visible
eye_contact = yes or no
usable = yes ONLY for person=camera with eye_contact=yes
reason = at most 10 words
"""
ALLOWED = {"camera", "phone", "notes", "away", "other_person", "none"}


def _ffmpeg() -> str:
    from .media import get_ffmpeg_exe

    return get_ffmpeg_exe()


def frame_bytes(video: str | Path, at: float, width: int = 640) -> bytes:
    """One JPEG frame at `at` seconds (scaled down: cheaper image tokens)."""
    tmp = Path(tempfile.mkdtemp(prefix="frame_")) / "f.jpg"
    subprocess.run([_ffmpeg(), "-y", "-v", "error", "-ss", f"{max(0.0, at):.3f}",
                    "-i", str(video), "-frames:v", "1",
                    "-vf", f"scale={width}:-2", str(tmp)],
                   check=True, capture_output=True)
    return tmp.read_bytes()


def parse_verdict(raw: str) -> dict[str, Any]:
    """Parse 'person|eye|usable|reason', tolerating extra prose lines."""
    line = ""
    for candidate in (raw or "").splitlines():
        if candidate.count("|") >= 1:
            line = candidate.strip()
            break
    parts = [p.strip().lower() for p in line.split("|")]
    person = parts[0] if parts and parts[0] in ALLOWED else "none"
    eye = len(parts) > 1 and parts[1].startswith("y")
    usable_flag = len(parts) > 2 and parts[2].startswith("y")
    reason = "|".join(parts[3:]) if len(parts) > 3 else ""
    return {"person": person, "eye_contact": eye,
            "usable": bool(usable_flag and person == "camera" and eye),
            "reason": reason[:80]}


def classify_frame(jpg: bytes, model: str = MODEL,
                   key: str | None = None) -> dict[str, Any]:
    """Ask the vision model what the person is doing in this frame."""
    import httpx

    from .transcribe_gemini import load_key

    key = key or load_key()
    body = {"contents": [{"role": "user", "parts": [
        {"text": PROMPT},
        {"inline_data": {"mime_type": "image/jpeg",
                         "data": base64.b64encode(jpg).decode()}}]}],
        "generationConfig": {"temperature": 0.0, "maxOutputTokens": 256}}
    with httpx.Client(timeout=120) as client:
        r = client.post(f"{API}/models/{model}:generateContent",
                        params={"key": key}, json=body)
    if r.status_code >= 400:
        raise RuntimeError(f"vision HTTP {r.status_code}: {r.text[:200]}")
    try:
        raw = r.json()["candidates"][0]["content"]["parts"][0].get("text", "")
    except (KeyError, IndexError):
        return {"person": "none", "eye_contact": False, "usable": False,
                "reason": "no text in the vision response"}
    return parse_verdict(raw)


def review_take(video: str | Path, start: float, end: float,
                samples: int = 3, model: str = MODEL,
                key: str | None = None) -> dict[str, Any]:
    """Sample a take and decide whether it is presentation-ready."""
    dur = max(0.2, end - start)
    n = max(1, samples)
    times = [start + dur * (i + 0.5) / n for i in range(n)]
    rows = []
    for t in times:
        try:
            rows.append({"t": round(t, 2),
                         **classify_frame(frame_bytes(video, t), model, key)})
        except Exception as exc:  # noqa: BLE001 - a blind sample is not a pass
            rows.append({"t": round(t, 2), "person": "unknown", "usable": False,
                         "eye_contact": False,
                         "reason": f"{type(exc).__name__}: {str(exc)[:60]}"})
    good = [r for r in rows if r.get("usable")]
    ratio = len(good) / len(rows) if rows else 0.0
    usable = ratio >= 0.66
    trim_start = None
    trim_end = None
    step = dur / n
    if rows and not rows[0]["usable"]:
        first_good = next((r for r in rows if r["usable"]), None)
        if first_good:
            trim_start = round(min(first_good["t"] - step * 0.5 + 0.05,
                                   end - 0.3), 2)
    if rows and not rows[-1]["usable"]:
        last_good = next((r for r in reversed(rows) if r["usable"]), None)
        if last_good:
            trim_end = round(max(last_good["t"] + step * 0.5 - 0.05,
                                 start + 0.3), 2)
    reasons = [r["reason"] for r in rows if not r["usable"] and r["reason"]]
    return {"usable": usable, "ratio": round(ratio, 2),
            "samples": rows, "trim_start": trim_start, "trim_end": trim_end,
            "reason": " / ".join(reasons[:2])}


def review_segments(video: str | Path, segments: list[dict[str, Any]],
                    samples: int = 3, model: str = MODEL,
                    log=print) -> list[dict[str, Any]]:
    """Review a list of {start,end,text} segments; returns one verdict each."""
    out = []
    for i, seg in enumerate(segments, 1):
        if seg.get("start") is None:
            out.append({"index": i, "skipped": "unmatched"})
            continue
        verdict = review_take(video, float(seg["start"]), float(seg["end"]),
                              samples=samples, model=model)
        out.append({"index": i, "start": seg["start"], "end": seg["end"],
                    "text": seg.get("text", ""), **verdict})
        state = "OK" if verdict["usable"] else f"PREP({verdict['ratio']})"
        log(f"[{i}/{len(segments)}] {state} {verdict['reason'][:60]}")
    return out


def save_review(project_dir: str | Path, rows: list[dict[str, Any]]) -> Path:
    path = Path(project_dir) / "work" / "vision_review.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path
