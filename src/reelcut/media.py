"""FFmpeg discovery, media probing, and CPU-friendly audio mastering."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class MediaInfo:
    """Basic media metadata extracted from video/audio files."""

    path: Path
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool


def get_ffmpeg_exe(explicit_path: str = "") -> str:
    """Resolve FFmpeg binary from config, env, PATH, or imageio-ffmpeg."""
    for candidate in (explicit_path, os.getenv("REELCUT_FFMPEG_PATH", "").strip()):
        if candidate and Path(candidate).is_file():
            return candidate

    on_path = shutil.which("ffmpeg")
    if on_path:
        return on_path

    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def get_ffprobe_exe() -> str | None:
    """Return ffprobe path if available on PATH."""
    return shutil.which("ffprobe")


def run_ffmpeg(args: list[str], ffmpeg_exe: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run an FFmpeg command and raise a readable error on failure."""
    exe = ffmpeg_exe or get_ffmpeg_exe()
    cmd = [exe, "-hide_banner", "-y", *args]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.splitlines()[-15:])
        raise RuntimeError(f"FFmpeg failed (code {proc.returncode}):\n{tail}")
    return proc


def _parse_fps(raw: str) -> float:
    if "/" in raw:
        num, den = raw.split("/", 1)
        try:
            d = float(den)
            return float(num) / d if d else 30.0
        except ValueError:
            return 30.0
    try:
        return float(raw)
    except ValueError:
        return 30.0


def probe_media(path: str | Path, ffmpeg_exe: str | None = None) -> MediaInfo:
    """Probe duration, dimensions, fps, and audio presence without requiring ffprobe."""
    file_path = Path(path).resolve()
    if not file_path.is_file():
        raise FileNotFoundError(f"Media file not found: {file_path}")

    ffprobe = get_ffprobe_exe()
    if ffprobe:
        proc = subprocess.run(
            [
                ffprobe,
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(file_path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode == 0:
            data = json.loads(proc.stdout)
            duration = float(data.get("format", {}).get("duration", 0.0) or 0.0)
            v_stream = next(
                (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
                {},
            )
            has_audio = any(
                s.get("codec_type") == "audio" for s in data.get("streams", [])
            )
            return MediaInfo(
                path=file_path,
                duration=duration,
                width=int(v_stream.get("width", 0) or 0),
                height=int(v_stream.get("height", 0) or 0),
                fps=_parse_fps(str(v_stream.get("r_frame_rate", "30/1"))),
                has_audio=has_audio,
            )

    # Fallback: parse `ffmpeg -i` stderr when ffprobe is absent (e.g. imageio-ffmpeg)
    exe = ffmpeg_exe or get_ffmpeg_exe()
    proc = subprocess.run(
        [exe, "-hide_banner", "-i", str(file_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    stderr = proc.stderr

    dur_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr)
    duration = 0.0
    if dur_match:
        h, m, s = dur_match.groups()
        duration = int(h) * 3600 + int(m) * 60 + float(s)

    width, height, fps = 0, 0, 30.0
    vid_match = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", stderr)
    if vid_match:
        width, height = int(vid_match.group(1)), int(vid_match.group(2))
    fps_match = re.search(r"(\d+(?:\.\d+)?)\s*fps", stderr)
    if fps_match:
        fps = float(fps_match.group(1))

    has_audio = bool(re.search(r"Stream #\d+:\d+.*Audio:", stderr))
    return MediaInfo(
        path=file_path,
        duration=duration,
        width=width,
        height=height,
        fps=fps,
        has_audio=has_audio,
    )


def extract_wav_16k(
    input_path: str | Path,
    out_wav: str | Path,
    ffmpeg_exe: str | None = None,
) -> Path:
    """Extract mono 16kHz PCM WAV for VAD and Whisper transcription."""
    out = Path(out_wav)
    out.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            "-i",
            str(input_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(out),
        ],
        ffmpeg_exe=ffmpeg_exe,
    )
    return out


def enhance_audio_to_wav(
    input_path: str | Path,
    out_wav: str | Path,
    ffmpeg_exe: str | None = None,
    gate_threshold_db: float | None = None,
) -> Path:
    """Clean, gate background chatter, compress, and normalize speech (-14 LUFS).

    Uses optional DeepFilterNet if installed in the environment, followed by
    FFmpeg adaptive gating/EQ/compression/loudnorm. Falls back cleanly to FFmpeg
    adaptive denoising (`afftdn`) on CPU-only setups.
    """
    out = Path(out_wav)
    out.parent.mkdir(parents=True, exist_ok=True)

    source_for_master = Path(input_path)
    temp_df_wav = out.with_suffix(".df48k.wav")
    used_df = False

    try:
        from df.enhance import enhance, init_df, load_audio, save_audio  # type: ignore

        raw_48k = out.with_suffix(".raw48k.wav")
        run_ffmpeg(
            ["-i", str(input_path), "-vn", "-ac", "1", "-ar", "48000", str(raw_48k)],
            ffmpeg_exe=ffmpeg_exe,
        )
        model, df_state, _ = init_df()
        audio, _ = load_audio(str(raw_48k), sr=df_state.sr())
        enhanced = enhance(model, df_state, audio)
        save_audio(str(temp_df_wav), enhanced, df_state.sr())
        raw_48k.unlink(missing_ok=True)
        source_for_master = temp_df_wav
        used_df = True
    except Exception:
        used_df = False

    filters = ["highpass=f=80", "lowpass=f=14500" if used_df else "lowpass=f=14000"]
    if not used_df:
        filters.append("afftdn=nf=-25:tn=1")
    if gate_threshold_db is not None:
        clamped_db = max(-52.0, min(-20.0, float(gate_threshold_db)))
        linear_thresh = 10.0 ** (clamped_db / 20.0)
        # Attenuate distant background voices during pauses before compression boosts them
        filters.append(
            f"agate=threshold={linear_thresh:.6f}:range=0.05:attack=8:release=140"
        )
    filters.extend(
        [
            "acompressor=threshold=-18dB:ratio=3:attack=5:release=60:makeup=2dB",
            "loudnorm=I=-14:TP=-1.5:LRA=11",
        ]
    )
    filter_chain = ",".join(filters)

    run_ffmpeg(
        [
            "-i",
            str(source_for_master),
            "-vn",
            "-af",
            filter_chain,
            "-ac",
            "1",
            "-ar",
            "48000",
            "-c:a",
            "pcm_s16le",
            str(out),
        ],
        ffmpeg_exe=ffmpeg_exe,
    )
    temp_df_wav.unlink(missing_ok=True)
    return out
