"""Main (on-camera) speaker verification using loudness, pitch (F0), and spectral timbre."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .vad import SpeechInterval, measure_interval_dbfs


@dataclass(slots=True)
class SpeakerProfile:
    """Acoustic profile of the primary (closest-to-camera) speaker."""

    ref_db: float
    ref_f0: float
    ref_spec: np.ndarray


def compute_pitch_f0(audio: np.ndarray, sample_rate: int = 16000) -> float:
    """Estimate median fundamental pitch F0 (Hz) across voiced 40ms frames."""
    frame_len = max(1, int(0.04 * sample_rate))
    hop = max(1, int(0.02 * sample_rate))
    if len(audio) < frame_len:
        return 0.0

    min_lag = max(1, int(sample_rate / 340))
    max_lag = min(frame_len - 1, int(sample_rate / 70))
    if max_lag <= min_lag:
        return 0.0

    pitches: list[float] = []
    for start in range(0, len(audio) - frame_len + 1, hop):
        frame = audio[start : start + frame_len]
        if float(np.sqrt(np.mean(frame**2))) < 0.012:
            continue
        centered = frame - float(np.mean(frame))
        ac = np.correlate(centered, centered, mode="full")[frame_len - 1 :]
        if ac[0] <= 1e-9:
            continue
        ac = ac / ac[0]
        window = ac[min_lag:max_lag]
        if len(window) == 0:
            continue
        pk = int(np.argmax(window))
        if window[pk] > 0.45:
            pitches.append(sample_rate / float(min_lag + pk))

    if not pitches:
        return 0.0
    return round(float(np.median(pitches)), 1)


def compute_spectral_profile(audio: np.ndarray, sample_rate: int = 16000, n_bands: int = 16) -> np.ndarray:
    """Compute a normalized 16-band log-spectral envelope vector for speaker timbre."""
    frame_len = 512
    hop = 256
    if len(audio) < frame_len:
        return np.zeros(n_bands, dtype=np.float32)

    window = np.hanning(frame_len).astype(np.float32)
    freqs = np.fft.rfftfreq(frame_len, d=1.0 / sample_rate)
    # Focus on 100 Hz - 4500 Hz where vocal tract formants live
    edges = np.geomspace(100.0, min(4500.0, sample_rate * 0.45), n_bands + 1)
    band_masks = [(freqs >= edges[i]) & (freqs < edges[i + 1]) for i in range(n_bands)]

    acc = np.zeros(n_bands, dtype=np.float64)
    count = 0
    for start in range(0, len(audio) - frame_len + 1, hop):
        frame = audio[start : start + frame_len]
        if float(np.sqrt(np.mean(frame**2))) < 0.012:
            continue
        spec = np.abs(np.fft.rfft(frame * window)) + 1e-7
        band_vals = np.array(
            [np.log10(float(np.mean(spec[m])) + 1e-7) if np.any(m) else -7.0 for m in band_masks],
            dtype=np.float64,
        )
        acc += band_vals
        count += 1

    if count == 0:
        return np.zeros(n_bands, dtype=np.float32)

    vec = (acc / count).astype(np.float32)
    vec = vec - float(np.mean(vec))
    norm = float(np.linalg.norm(vec))
    return (vec / norm) if norm > 1e-8 else vec


def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na < 1e-8 or nb < 1e-8:
        return 1.0
    return float(np.dot(a, b) / (na * nb))


def estimate_primary_speaker_profile(
    audio_intervals: list[tuple[np.ndarray, int, list[SpeechInterval]]],
) -> SpeakerProfile:
    """Build the acoustic reference profile from the loudest (on-camera) speech cluster."""
    samples: list[tuple[float, float, np.ndarray, float]] = []
    for audio, sr, intervals in audio_intervals:
        for iv in intervals:
            s0 = max(0, int(iv.start * sr))
            s1 = min(len(audio), int(iv.end * sr))
            seg = audio[s0:s1]
            if len(seg) < int(0.25 * sr):
                continue
            db = measure_interval_dbfs(audio, sr, iv.start, iv.end)
            f0 = compute_pitch_f0(seg, sr)
            spec = compute_spectral_profile(seg, sr)
            samples.append((db, f0, spec, max(0.25, iv.end - iv.start)))

    if not samples:
        return SpeakerProfile(ref_db=-30.0, ref_f0=0.0, ref_spec=np.zeros(16, dtype=np.float32))

    dbs = np.array([s[0] for s in samples], dtype=np.float32)
    top_db = float(np.percentile(dbs, 90))
    # Loudest cluster within 4.5 dB of top 90th percentile represents the closest/on-camera speaker
    loud_cluster = [s for s in samples if s[0] >= top_db - 4.5]
    if not loud_cluster:
        loud_cluster = samples

    ref_db = round(float(np.median([s[0] for s in loud_cluster])), 2)
    voiced_f0s = [s[1] for s in loud_cluster if s[1] > 0.0]
    ref_f0 = round(float(np.median(voiced_f0s)), 1) if voiced_f0s else 0.0

    spec_sum = np.sum([s[2] * s[3] for s in loud_cluster], axis=0)
    spec_norm = float(np.linalg.norm(spec_sum))
    ref_spec = (spec_sum / spec_norm).astype(np.float32) if spec_norm > 1e-8 else np.zeros(16, dtype=np.float32)

    return SpeakerProfile(ref_db=ref_db, ref_f0=ref_f0, ref_spec=ref_spec)


def compute_face_motion_score(
    video_path: str | Path,
    start: float,
    end: float,
    num_samples: int = 6,
) -> float:
    """Measure center-frame face/lip motion energy across [start, end] using PyAV.

    Returns -1.0 if the video stream cannot be decoded.
    """
    try:
        import av

        dur = max(0.2, end - start)
        step = dur / max(2, num_samples)
        frames_gray: list[np.ndarray] = []

        with av.open(str(video_path)) as container:
            v_stream = next((s for s in container.streams if s.type == "video"), None)
            if v_stream is None:
                return -1.0
            v_stream.thread_type = "AUTO"
            tb = float(v_stream.time_base) if v_stream.time_base else 1.0 / 30.0
            container.seek(int(max(0.0, start) / tb), stream=v_stream, any_frame=False, backward=True)

            next_target = start
            for frame in container.decode(v_stream):
                t = float(frame.pts * tb) if frame.pts is not None else next_target
                if t < next_target - 0.02:
                    continue
                if t > end + 0.1 or len(frames_gray) >= num_samples:
                    break
                arr = frame.to_ndarray(format="gray")
                h, w = arr.shape
                # Crop central face/mouth region (30%..70% width, 20%..70% height)
                crop = arr[int(h * 0.20) : int(h * 0.70), int(w * 0.30) : int(w * 0.70)]
                if crop.size > 0:
                    # Downsample 4x for fast noise-tolerant L1 comparison
                    frames_gray.append(crop[::4, ::4].astype(np.float32))
                next_target = t + step

        if len(frames_gray) < 2:
            return -1.0
        diffs = [
            float(np.mean(np.abs(frames_gray[i + 1] - frames_gray[i])))
            for i in range(len(frames_gray) - 1)
        ]
        return round(float(np.median(diffs)), 3)
    except Exception:
        return -1.0


def is_primary_speaker_segment(
    seg_audio: np.ndarray,
    sample_rate: int,
    seg_db: float,
    profile: SpeakerProfile,
    motion_score: float = -1.0,
    min_motion_threshold: float | None = None,
) -> bool:
    """Return True if an audio segment belongs to the primary on-camera speaker."""
    # Hard floor: far background chatter or room noise
    if seg_db < profile.ref_db - 12.5:
        return False

    seg_f0 = compute_pitch_f0(seg_audio, sample_rate)
    seg_spec = compute_spectral_profile(seg_audio, sample_rate)
    sim = _cosine_sim(seg_spec, profile.ref_spec)

    # Distinct vocal pitch (e.g., female behind-camera prompter vs male creator or vice versa)
    if profile.ref_f0 > 0.0 and seg_f0 > 0.0:
        pitch_diff_ratio = abs(seg_f0 - profile.ref_f0) / profile.ref_f0
        if pitch_diff_ratio > 0.35 and (seg_db < profile.ref_db - 3.0 or sim < 0.92):
            return False

    # Same-gender off-camera speaker (quieter + different spectral envelope)
    if seg_db < profile.ref_db - 7.5 and sim < 0.90:
        return False

    # Visual active-speaker check: reject still-face listening segments when quieter than creator
    if (
        min_motion_threshold is not None
        and motion_score >= 0.0
        and motion_score < min_motion_threshold
        and seg_db < profile.ref_db - 2.0
    ):
        return False

    return True


def filter_primary_speaker_intervals(
    audio: np.ndarray,
    sample_rate: int,
    intervals: list[SpeechInterval],
    profile: SpeakerProfile,
) -> list[SpeechInterval]:
    """Split/filter VAD intervals to retain only the primary on-camera speaker."""
    kept: list[SpeechInterval] = []
    for iv in intervals:
        s0 = max(0, int(iv.start * sample_rate))
        s1 = min(len(audio), int(iv.end * sample_rate))
        seg = audio[s0:s1]
        db = measure_interval_dbfs(audio, sample_rate, iv.start, iv.end)
        if is_primary_speaker_segment(seg, sample_rate, db, profile):
            kept.append(iv)
    return kept
