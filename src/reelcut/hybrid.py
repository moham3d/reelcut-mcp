"""Low-token hybrid AI packager: packs VAD speech audio + 360p thumbnails without local Whisper."""

from __future__ import annotations

import json
import wave
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .brolls import load_broll_index
from .config import ReelcutConfig
from .media import extract_wav_16k, get_ffmpeg_exe, probe_media, run_ffmpeg
from .models import ClipSummary
from .speaker import (
    compute_face_motion_score,
    compute_pitch_f0,
    estimate_primary_speaker_profile,
    filter_primary_speaker_intervals,
)
from .transcribe import _read_script, find_input_clips
from .vad import detect_speech_segments, load_pcm16_mono, measure_interval_dbfs


def _save_midpoint_thumbnail(
    video_path: Path,
    midpoint_sec: float,
    out_jpg: Path,
    max_width: int = 360,
) -> bool:
    """Extract one compact 360p JPEG thumbnail at `midpoint_sec` using PyAV + Pillow."""
    try:
        import av

        out_jpg.parent.mkdir(parents=True, exist_ok=True)
        with av.open(str(video_path)) as container:
            v_stream = next((s for s in container.streams if s.type == "video"), None)
            if v_stream is None:
                return False
            tb = float(v_stream.time_base) if v_stream.time_base else 1.0 / 30.0
            container.seek(int(max(0.0, midpoint_sec) / tb), stream=v_stream, backward=True)
            for frame in container.decode(v_stream):
                ft = float(frame.pts * tb) if frame.pts is not None else midpoint_sec
                if ft >= midpoint_sec - 0.1:
                    img = frame.to_image()
                    if img.width > max_width:
                        ratio = max_width / float(img.width)
                        img = img.resize(
                            (max_width, max(1, int(round(img.height * ratio)))),
                            Image.Resampling.BILINEAR,
                        )
                    img.save(out_jpg, format="JPEG", quality=75)
                    return True
        return False
    except Exception:
        return False


def prepare_hybrid_pack(
    project_dir: str | Path,
    brolls_dir: str | Path | None = None,
    include_thumbnails: bool = True,
) -> dict[str, Any]:
    """Prepare a low-token multimodal bundle (packed speech audio + 360p thumbnails).

    Skips slow CPU Whisper transcription: uses Silero VAD + primary-speaker
    filtering locally (~5-10s) to pack ONLY main-speaker speech into a single
    compact MP3 (`work/hybrid_speech.mp3`) plus midpoint thumbnails and an exact
    timestamp mapping (`work/hybrid_map.json`).
    """
    proj = Path(project_dir).resolve()
    if not proj.is_dir():
        raise FileNotFoundError(f"Project directory not found: {proj}")

    cfg = ReelcutConfig.load(proj)
    ffmpeg_exe = get_ffmpeg_exe(cfg.ffmpeg_path)
    clips = find_input_clips(proj)
    if not clips:
        raise FileNotFoundError(f"No video clips found in {proj / 'clips'} or {proj}.")

    work_dir = proj / "work"
    vad_dir = work_dir / "vad"
    thumbs_dir = work_dir / "thumbs"
    vad_dir.mkdir(parents=True, exist_ok=True)
    if include_thumbnails:
        thumbs_dir.mkdir(parents=True, exist_ok=True)

    clip_data: list[dict[str, Any]] = []
    for idx, clip_path in enumerate(clips, start=1):
        clip_id = f"c{idx:02d}"
        info = probe_media(clip_path, ffmpeg_exe=ffmpeg_exe)
        if not info.has_audio:
            continue
        wav_16k = vad_dir / f"{clip_id}_16k.wav"
        clip_mtime = clip_path.stat().st_mtime
        if not wav_16k.is_file() or wav_16k.stat().st_mtime < clip_mtime:
            extract_wav_16k(clip_path, wav_16k, ffmpeg_exe=ffmpeg_exe)

        raw_audio, raw_sr = load_pcm16_mono(wav_16k)
        ivs = detect_speech_segments(wav_16k, min_silence_ms=220, pad_ms=80)
        clip_data.append(
            {
                "clip_id": clip_id,
                "clip_path": clip_path,
                "info": info,
                "raw_audio": raw_audio,
                "raw_sr": raw_sr,
                "ivs": ivs,
            }
        )

    profile = estimate_primary_speaker_profile(
        [(c["raw_audio"], c["raw_sr"], c["ivs"]) for c in clip_data]
    )

    packed_chunks: list[np.ndarray] = []
    gap_samples = np.zeros(int(0.35 * 16000), dtype=np.float32)
    packed_cursor = 0.0
    mapped_intervals: list[dict[str, Any]] = []
    clip_summaries: list[ClipSummary] = []
    clips_meta: dict[str, Any] = {}

    for c in clip_data:
        clip_id = c["clip_id"]
        clip_path = c["clip_path"]
        info = c["info"]
        raw_audio = c["raw_audio"]
        raw_sr = c["raw_sr"]

        clip_summaries.append(
            ClipSummary(
                clip_id=clip_id,
                filename=clip_path.name,
                duration=round(info.duration, 2),
                width=info.width,
                height=info.height,
            )
        )
        clips_meta[clip_id] = {"path": str(clip_path), "enhanced_wav": str(work_dir / "audio" / f"{clip_id}.wav")}

        kept = filter_primary_speaker_intervals(raw_audio, raw_sr, c["ivs"], profile)
        for v_idx, iv in enumerate(kept, start=1):
            s0 = max(0, int(iv.start * raw_sr))
            s1 = min(len(raw_audio), int(iv.end * raw_sr))
            slice_arr = raw_audio[s0:s1]
            if len(slice_arr) < int(0.35 * raw_sr):
                continue

            db = measure_interval_dbfs(raw_audio, raw_sr, iv.start, iv.end)
            f0 = compute_pitch_f0(slice_arr, raw_sr)
            motion = compute_face_motion_score(clip_path, iv.start, iv.end)
            dur = len(slice_arr) / float(raw_sr)

            iv_id = f"{clip_id}_v{v_idx:02d}"
            thumb_rel: str | None = None
            if include_thumbnails:
                thumb_file = thumbs_dir / f"{iv_id}.jpg"
                if _save_midpoint_thumbnail(clip_path, (iv.start + iv.end) / 2.0, thumb_file):
                    thumb_rel = str(thumb_file)

            packed_chunks.append(slice_arr)
            packed_chunks.append(gap_samples)

            mapped_intervals.append(
                {
                    "id": iv_id,
                    "clip_id": clip_id,
                    "packed_start": round(packed_cursor, 3),
                    "packed_end": round(packed_cursor + dur, 3),
                    "source_start": round(iv.start, 3),
                    "source_end": round(iv.end, 3),
                    "db": db,
                    "f0": f0,
                    "motion": motion,
                    "thumbnail": thumb_rel,
                }
            )
            packed_cursor += dur + 0.35

    packed_wav = work_dir / "hybrid_speech.wav"
    packed_mp3 = work_dir / "hybrid_speech.mp3"
    if packed_chunks:
        audio_f32 = np.clip(np.concatenate(packed_chunks), -1.0, 1.0)
        pcm16 = (audio_f32 * 32767.0).astype(np.int16)
        with wave.open(str(packed_wav), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(pcm16.tobytes())
        run_ffmpeg(
            ["-i", str(packed_wav), "-c:a", "libmp3lame", "-b:a", "48k", str(packed_mp3)],
            ffmpeg_exe=ffmpeg_exe,
        )
        packed_wav.unlink(missing_ok=True)

    # Also write minimal words_detail.json so save_edit_plan works seamlessly right after
    (work_dir / "words_detail.json").write_text(
        json.dumps({"clips": clips_meta, "segments": {}}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    script_text = _read_script(proj)
    resolved_brolls_dir = Path(brolls_dir).resolve() if brolls_dir else cfg.brolls_dir
    local_brolls = load_broll_index(proj, resolved_brolls_dir)

    pack_payload = {
        "project_dir": str(proj),
        "mode": "hybrid_ai_pack",
        "speech_audio_mp3": str(packed_mp3) if packed_mp3.is_file() else None,
        "packed_duration_sec": round(packed_cursor, 2),
        "speaker_profile": {"ref_db": profile.ref_db, "ref_f0": profile.ref_f0},
        "has_script": script_text is not None,
        "script_text": script_text,
        "clips": [c.model_dump() for c in clip_summaries],
        "intervals": mapped_intervals,
        "local_brolls": [
            {
                "broll_id": b["broll_id"],
                "tags": b.get("tags", []),
                "duration": b.get("duration", 0.0),
            }
            for b in local_brolls
        ],
    }

    (work_dir / "hybrid_map.json").write_text(
        json.dumps(pack_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return pack_payload
