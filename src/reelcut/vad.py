"""Voice Activity Detection (Silero VAD via ONNX) and clean segment trimming."""

from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(slots=True)
class SpeechInterval:
    """Detected speech interval in seconds."""

    start: float
    end: float


def load_pcm16_mono(wav_path: str | Path) -> tuple[np.ndarray, int]:
    """Load a mono 16-bit PCM WAV file into float32 samples in [-1, 1]."""
    with wave.open(str(wav_path), "rb") as wf:
        sample_rate = wf.getframerate()
        channels = wf.getnchannels()
        frames = wf.readframes(wf.getnframes())
    pcm = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        pcm = pcm.reshape(-1, channels).mean(axis=1)
    return pcm, sample_rate


def measure_interval_dbfs(audio: np.ndarray, sample_rate: int, start: float, end: float) -> float:
    """Compute speech RMS loudness in dBFS (active 75th-percentile frame energy)."""
    s_idx = max(0, int(start * sample_rate))
    e_idx = min(len(audio), int(end * sample_rate))
    chunk = audio[s_idx:e_idx]
    if len(chunk) == 0:
        return -90.0

    frame_len = max(1, int(sample_rate * 0.03))
    n_frames = len(chunk) // frame_len
    if n_frames == 0:
        rms = float(np.sqrt(np.mean(chunk**2)))
    else:
        frames = chunk[: n_frames * frame_len].reshape(n_frames, frame_len)
        frame_rms = np.sqrt(np.mean(frames**2, axis=1))
        rms = float(np.percentile(frame_rms, 90))

    return round(float(20.0 * np.log10(max(rms, 1e-6))), 2)


def estimate_main_speaker_dbfs(
    audio: np.ndarray,
    sample_rate: int,
    intervals: list[SpeechInterval],
) -> float:
    """Estimate the main (closest-to-mic) speaker loudness in dBFS."""
    if not intervals:
        return measure_interval_dbfs(audio, sample_rate, 0.0, len(audio) / max(1, sample_rate))
    levels = [measure_interval_dbfs(audio, sample_rate, iv.start, iv.end) for iv in intervals]
    return round(float(np.percentile(levels, 85)), 2)


def _merge_and_pad(
    intervals: list[SpeechInterval],
    total_duration: float,
    pad_sec: float,
    min_gap_sec: float,
) -> list[SpeechInterval]:
    if not intervals:
        return []

    padded = [
        SpeechInterval(
            start=max(0.0, iv.start - pad_sec),
            end=min(total_duration, iv.end + pad_sec),
        )
        for iv in intervals
    ]
    merged: list[SpeechInterval] = [padded[0]]
    for iv in padded[1:]:
        prev = merged[-1]
        if iv.start - prev.end <= min_gap_sec:
            prev.end = max(prev.end, iv.end)
        else:
            merged.append(iv)
    return merged


def _energy_vad_fallback(
    audio: np.ndarray,
    sample_rate: int,
    min_silence_ms: int,
    min_speech_ms: int,
    pad_ms: int,
) -> list[SpeechInterval]:
    """Lightweight RMS energy VAD fallback when Silero VAD is unavailable."""
    frame_len = max(1, int(sample_rate * 0.03))
    n_frames = len(audio) // frame_len
    if n_frames == 0:
        return []

    trimmed = audio[: n_frames * frame_len].reshape(n_frames, frame_len)
    rms = np.sqrt(np.mean(trimmed**2, axis=1))
    peak = float(np.percentile(rms, 95)) if len(rms) else 0.0
    threshold = max(0.008, peak * 0.12)
    active = rms >= threshold

    intervals: list[SpeechInterval] = []
    start_idx: int | None = None
    frame_sec = frame_len / sample_rate
    for idx, is_speech in enumerate(active):
        if is_speech and start_idx is None:
            start_idx = idx
        elif not is_speech and start_idx is not None:
            dur_ms = (idx - start_idx) * frame_sec * 1000.0
            if dur_ms >= min_speech_ms:
                intervals.append(
                    SpeechInterval(start=start_idx * frame_sec, end=idx * frame_sec)
                )
            start_idx = None

    if start_idx is not None:
        dur_ms = (n_frames - start_idx) * frame_sec * 1000.0
        if dur_ms >= min_speech_ms:
            intervals.append(
                SpeechInterval(start=start_idx * frame_sec, end=n_frames * frame_sec)
            )

    total_dur = len(audio) / sample_rate
    return _merge_and_pad(intervals, total_dur, pad_ms / 1000.0, min_silence_ms / 1000.0)


def detect_speech_segments(
    wav_16k_path: str | Path,
    min_silence_ms: int = 350,
    min_speech_ms: int = 250,
    pad_ms: int = 110,
) -> list[SpeechInterval]:
    """Detect speech segments on CPU using Silero VAD (ONNX) with energy fallback."""
    audio, sr = load_pcm16_mono(wav_16k_path)
    if len(audio) == 0:
        return []
    total_dur = len(audio) / sr

    try:
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        vad_opts = VadOptions(
            threshold=0.45,
            min_speech_duration_ms=min_speech_ms,
            min_silence_duration_ms=min_silence_ms,
            speech_pad_ms=pad_ms,
        )
        raw_stamps = get_speech_timestamps(audio, vad_options=vad_opts, sampling_rate=sr)
        intervals = [
            SpeechInterval(
                start=round(max(0.0, item["start"] / sr), 3),
                end=round(min(total_dur, item["end"] / sr), 3),
            )
            for item in raw_stamps
            if item["end"] > item["start"]
        ]
        return _merge_and_pad(
            intervals,
            total_dur,
            pad_sec=0.0,
            min_gap_sec=min_silence_ms / 1000.0,
        )
    except Exception:
        return _energy_vad_fallback(audio, sr, min_silence_ms, min_speech_ms, pad_ms)
