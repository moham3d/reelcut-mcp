"""CPU-friendly transcription and token-efficient project preparation."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .brolls import load_broll_index
from .config import ReelcutConfig
from .media import enhance_audio_to_wav, extract_wav_16k, get_ffmpeg_exe, probe_media
from .models import ClipSummary, CompactSegment
from .speaker import estimate_primary_speaker_profile, is_primary_speaker_segment
from .vad import (
    SpeechInterval,
    detect_speech_segments,
    load_pcm16_mono,
    measure_interval_dbfs,
)

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}


def _natural_sort_key(path: Path) -> list[Any]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.name)]


def find_input_clips(project_dir: Path) -> list[Path]:
    """Find raw talking-head clips inside `<project_dir>/clips` (or `<project_dir>`)."""
    clips_dir = project_dir / "clips"
    search_dir = clips_dir if clips_dir.is_dir() else project_dir
    files = [
        p
        for p in search_dir.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    ]
    return sorted(files, key=_natural_sort_key)


def _read_script(project_dir: Path) -> str | None:
    for name in ("script.txt", "script_resources.txt", "script.md"):
        candidate = project_dir / name
        if candidate.is_file():
            text = candidate.read_text(encoding="utf-8", errors="replace").strip()
            if text:
                return text
    return None


def _build_whisper_prompt(script_text: str | None, language: str = "ar") -> str | None:
    """Create a short script hint without URLs/ASCII drift to lock Whisper onto Arabic."""
    if not script_text:
        return None
    cleaned = re.sub(r"https?://\S+", " ", script_text)
    if language == "ar":
        # Avoid Latin tokens like 'DNA' tempting Whisper into English translation
        cleaned = re.sub(r"[A-Za-z]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:220] if cleaned else None


def _get_whisper_model(model_size: str, compute_type: str):
    from faster_whisper import WhisperModel

    # Leave 2 CPU threads free on Windows so the desktop stays responsive
    total_cores = os.cpu_count() or 4
    cpu_threads = max(1, total_cores - 2)
    return WhisperModel(
        model_size,
        device="cpu",
        compute_type=compute_type,
        cpu_threads=cpu_threads,
    )


def prepare_project(
    project_dir: str | Path,
    whisper_model: str | None = None,
    auto_trim_silence: bool = True,
    brolls_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Prepare clips locally (enhance audio, VAD silence trim, Whisper transcription).

    Saves detailed word timings locally in `work/words_detail.json` and returns
    a compact token-efficient summary (`work/transcript_compact.json`) for the AI.
    """
    proj = Path(project_dir).resolve()
    if not proj.is_dir():
        raise FileNotFoundError(f"Project directory not found: {proj}")

    cfg = ReelcutConfig.load(proj)
    model_name = whisper_model or cfg.whisper_model
    ffmpeg_exe = get_ffmpeg_exe(cfg.ffmpeg_path)

    clips = find_input_clips(proj)
    if not clips:
        raise FileNotFoundError(
            f"No video clips found in {proj / 'clips'} or {proj}. "
            "Place raw clips inside a 'clips/' subfolder."
        )

    work_dir = proj / "work"
    audio_dir = work_dir / "audio"
    vad_dir = work_dir / "vad"
    audio_dir.mkdir(parents=True, exist_ok=True)
    vad_dir.mkdir(parents=True, exist_ok=True)

    script_text = _read_script(proj)
    whisper_prompt = _build_whisper_prompt(script_text, language=cfg.language)

    model = _get_whisper_model(model_name, cfg.whisper_compute)

    # Pass 1: Extract raw 16kHz audio and VAD intervals for all clips so we build
    # one consistent voiceprint of the main (on-camera) speaker across the project.
    clip_data: list[dict[str, Any]] = []
    for idx, clip_path in enumerate(clips, start=1):
        clip_id = f"c{idx:02d}"
        info = probe_media(clip_path, ffmpeg_exe=ffmpeg_exe)
        if not info.has_audio:
            continue

        enhanced_wav = audio_dir / f"{clip_id}.wav"
        wav_16k = vad_dir / f"{clip_id}_16k.wav"
        clip_mtime = clip_path.stat().st_mtime
        if not wav_16k.is_file() or wav_16k.stat().st_mtime < clip_mtime:
            extract_wav_16k(clip_path, wav_16k, ffmpeg_exe=ffmpeg_exe)

        raw_audio, raw_sr = load_pcm16_mono(wav_16k)
        vad_intervals = (
            detect_speech_segments(wav_16k, min_silence_ms=180, pad_ms=60)
            if auto_trim_silence
            else []
        )
        clip_data.append(
            {
                "clip_id": clip_id,
                "clip_path": clip_path,
                "info": info,
                "enhanced_wav": enhanced_wav,
                "wav_16k": wav_16k,
                "raw_audio": raw_audio,
                "raw_sr": raw_sr,
                "vad_intervals": vad_intervals,
            }
        )

    speaker_profile = estimate_primary_speaker_profile(
        [(c["raw_audio"], c["raw_sr"], c["vad_intervals"]) for c in clip_data]
    )

    clip_summaries: list[ClipSummary] = []
    compact_segments: list[CompactSegment] = []
    words_detail: dict[str, Any] = {}

    for c in clip_data:
        clip_id = c["clip_id"]
        clip_path = c["clip_path"]
        info = c["info"]
        enhanced_wav = c["enhanced_wav"]
        raw_audio = c["raw_audio"]
        raw_sr = c["raw_sr"]
        vad_intervals: list[SpeechInterval] = c["vad_intervals"]

        clip_summaries.append(
            ClipSummary(
                clip_id=clip_id,
                filename=clip_path.name,
                duration=round(info.duration, 2),
                width=info.width,
                height=info.height,
            )
        )

        gate_db = speaker_profile.ref_db - 10.5
        clip_mtime = clip_path.stat().st_mtime
        if not enhanced_wav.is_file() or enhanced_wav.stat().st_mtime < clip_mtime:
            enhance_audio_to_wav(
                clip_path,
                enhanced_wav,
                ffmpeg_exe=ffmpeg_exe,
                gate_threshold_db=gate_db,
            )

        # Run Whisper once on natural raw audio with tight pause splitting (180ms),
        # then split any merged segment at >= 0.25s pauses and verify the primary speaker.
        segments_iter, _ = model.transcribe(
            raw_audio,
            language=cfg.language,
            initial_prompt=whisper_prompt,
            beam_size=2,
            best_of=1,
            condition_on_previous_text=False,
            repetition_penalty=1.15,
            no_speech_threshold=0.65,
            log_prob_threshold=-1.5,
            compression_ratio_threshold=2.2,
            word_timestamps=True,
            vad_filter=auto_trim_silence,
            vad_parameters={
                "min_silence_duration_ms": 180,
                "speech_pad_ms": 60,
            },
        )

        seg_counter = 0
        for seg in segments_iter:
            raw_words = [
                {
                    "word": (w.word or "").strip(),
                    "start": round(float(w.start), 3),
                    "end": round(float(w.end), 3),
                }
                for w in (getattr(seg, "words", None) or [])
                if (w.word or "").strip()
            ]

            # Split at >= 0.25s internal pauses so prompter & creator turns are checked separately
            turns: list[tuple[float, float, str, list[dict[str, Any]]]] = []
            if raw_words:
                cur_words: list[dict[str, Any]] = []
                for w in raw_words:
                    if cur_words and (w["start"] - cur_words[-1]["end"]) >= 0.25:
                        turns.append(
                            (
                                cur_words[0]["start"],
                                cur_words[-1]["end"],
                                " ".join(x["word"] for x in cur_words),
                                cur_words,
                            )
                        )
                        cur_words = []
                    cur_words.append(w)
                if cur_words:
                    turns.append(
                        (
                            cur_words[0]["start"],
                            cur_words[-1]["end"],
                            " ".join(x["word"] for x in cur_words),
                            cur_words,
                        )
                    )
            else:
                text = seg.text.strip()
                if text:
                    turns.append((round(float(seg.start), 3), round(float(seg.end), 3), text, []))

            for seg_start, seg_end, text, seg_words in turns:
                if not text or seg_end <= seg_start + 0.1:
                    continue

                w0 = max(0, int(seg_start * raw_sr))
                w1 = min(len(raw_audio), int(seg_end * raw_sr))
                seg_db = measure_interval_dbfs(raw_audio, raw_sr, seg_start, seg_end)
                if not is_primary_speaker_segment(raw_audio[w0:w1], raw_sr, seg_db, speaker_profile):
                    continue

                seg_counter += 1
                seg_id = f"{clip_id}_s{seg_counter:02d}"

                compact_segments.append(
                    CompactSegment(
                        id=seg_id,
                        clip_id=clip_id,
                        start=seg_start,
                        end=seg_end,
                        text=text,
                    )
                )
                words_detail[seg_id] = {
                    "clip_id": clip_id,
                    "clip_path": str(clip_path),
                    "enhanced_wav": str(enhanced_wav),
                    "start": seg_start,
                    "end": seg_end,
                    "words": seg_words,
                }

    resolved_brolls_dir = Path(brolls_dir).resolve() if brolls_dir else cfg.brolls_dir
    local_brolls = load_broll_index(proj, resolved_brolls_dir)

    compact_payload = {
        "project_dir": str(proj),
        "whisper_model": model_name,
        "has_script": script_text is not None,
        "script_text": script_text,
        "clips": [c.model_dump() for c in clip_summaries],
        "segments": [s.model_dump() for s in compact_segments],
        "local_brolls": [
            {
                "broll_id": b["broll_id"],
                "tags": b.get("tags", []),
                "duration": b.get("duration", 0.0),
            }
            for b in local_brolls
        ],
    }

    (work_dir / "transcript_compact.json").write_text(
        json.dumps(compact_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (work_dir / "words_detail.json").write_text(
        json.dumps(
            {
                "clips": {
                    c.clip_id: {
                        "path": str(clips[i]),
                        "enhanced_wav": str(audio_dir / f"{c.clip_id}.wav"),
                    }
                    for i, c in enumerate(clip_summaries)
                },
                "segments": words_detail,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return compact_payload
