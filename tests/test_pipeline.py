"""End-to-end synthetic tests for ReelCut (audio, VAD, captions, planner, render)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from PIL import Image

from reelcut.brolls import index_local_brolls
from reelcut.captions import _shape_arabic_word, build_caption_concat_manifest
from reelcut.media import enhance_audio_to_wav, extract_wav_16k, get_ffmpeg_exe, probe_media
from reelcut.planner import save_edit_plan
from reelcut.renderer import render_video
from reelcut.vad import detect_speech_segments


def _make_synthetic_clip(out_mp4: Path, duration: float = 4.0) -> None:
    ffmpeg = get_ffmpeg_exe()
    # Generate a 720x1280 synthetic talking-head color video + 440Hz tone bursts (speech-like)
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size=720x1280:rate=30:duration={duration}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=220:sample_rate=48000:duration={duration}",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(out_mp4),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_arabic_shaping_and_caption_manifest(tmp_path: Path) -> None:
    shaped = _shape_arabic_word("المحتوى")
    assert shaped and isinstance(shaped, str)

    from reelcut.config import find_default_arabic_font

    font_path = find_default_arabic_font()
    words = [
        {"word": "ازيكم", "start": 0.1, "end": 0.5},
        {"word": "النهارده", "start": 0.5, "end": 1.0},
        {"word": "هنعمل", "start": 1.0, "end": 1.4},
        {"word": "فيديو", "start": 1.4, "end": 1.9},
        {"word": "جامد", "start": 1.9, "end": 2.4},
    ]
    manifest = build_caption_concat_manifest(
        timeline_words=words,
        total_duration=2.6,
        work_dir=tmp_path,
        font_path=font_path,
    )
    assert manifest.is_file()
    text = manifest.read_text(encoding="utf-8")
    assert "ffconcat version 1.0" in text
    pngs = list((tmp_path / "captions_frames").glob("cap_*.png"))
    assert len(pngs) == 5


def test_synthetic_end_to_end_pipeline(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    clips_dir = proj / "clips"
    brolls_dir = proj / "brolls"
    work_dir = proj / "work"
    audio_dir = work_dir / "audio"
    vad_dir = work_dir / "vad"
    for d in (clips_dir, brolls_dir, audio_dir, vad_dir):
        d.mkdir(parents=True, exist_ok=True)

    clip1 = clips_dir / "01.mp4"
    _make_synthetic_clip(clip1, duration=4.0)

    # Create a synthetic image B-roll
    broll_img = brolls_dir / "coding_screen.png"
    Image.new("RGB", (1080, 1920), (30, 60, 110)).save(broll_img)
    indexed = index_local_brolls(brolls_dir)
    assert len(indexed) == 1
    assert "coding" in indexed[0]["tags"]

    info = probe_media(clip1)
    assert info.has_audio
    assert info.duration >= 3.5

    wav16 = extract_wav_16k(clip1, vad_dir / "c01_16k.wav")
    enhanced = enhance_audio_to_wav(clip1, audio_dir / "c01.wav")
    intervals = detect_speech_segments(wav16)
    # Silero VAD filters out non-speech pure sine tones; verify valid interval list
    assert isinstance(intervals, list)

    # Write local words_detail.json simulating faster-whisper output
    words_detail = {
        "clips": {
            "c01": {
                "path": str(clip1),
                "enhanced_wav": str(enhanced),
            }
        },
        "segments": {
            "c01_s01": {
                "clip_id": "c01",
                "clip_path": str(clip1),
                "enhanced_wav": str(enhanced),
                "start": 0.1,
                "end": 3.5,
                "words": [
                    {"word": "ازيكم", "start": 0.2, "end": 0.7},
                    {"word": "النهارده", "start": 0.8, "end": 1.4},
                    {"word": "هنجرب", "start": 1.5, "end": 2.2},
                    {"word": "الاداة", "start": 2.3, "end": 2.9},
                    {"word": "الجديدة", "start": 2.9, "end": 3.4},
                ],
            }
        },
    }
    (work_dir / "words_detail.json").write_text(
        json.dumps(words_detail, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    plan_res = save_edit_plan(
        proj,
        {
            "title": "تجربة",
            "segments": [
                {
                    "clip_id": "c01",
                    "start": 0.1,
                    "end": 1.8,
                    "corrected_text": "أهلاً بيكم النهارده",
                    "zoom": False,
                },
                {
                    "clip_id": "c01",
                    "start": 1.8,
                    "end": 3.5,
                    "corrected_text": "هنجرب الأداة الجديدة",
                    "zoom": True,
                    "broll": {
                        "broll_id": indexed[0]["broll_id"],
                        "offset_sec": 0.2,
                        "duration_sec": 1.0,
                    },
                    "qr_url": "https://pubmed.ncbi.nlm.nih.gov/12409235/",
                    "qr_label": "المصدر",
                },
            ],
        },
    )
    assert plan_res["status"] == "plan_saved_awaiting_approval"
    assert plan_res["qr_count"] == 1
    assert "\n" in plan_res["summary_2_lines"]

    render_res = render_video(proj, backend="ffmpeg")
    assert render_res["status"] == "rendered"
    assert render_res["resolution"] == "1080x1920"
    out_mp4 = Path(render_res["output_path"])
    assert out_mp4.is_file() and out_mp4.stat().st_size > 10000

    from reelcut.config import find_filmcraft_cli

    if find_filmcraft_cli():
        fc_res = render_video(proj, backend="filmcraft")
        assert fc_res["status"] == "rendered"
        assert fc_res["backend"] == "filmcraft"
        assert Path(fc_res["project_path"]).is_file()
        assert Path(fc_res["output_path"]).is_file()
        assert fc_res["resolution"] == "1080x1920"


def test_background_speaker_loudness_gate() -> None:
    import numpy as np
    from reelcut.vad import SpeechInterval, estimate_main_speaker_dbfs, measure_interval_dbfs

    sr = 16000
    t = np.linspace(0, 1.0, sr, endpoint=False, dtype=np.float32)
    loud_main = 0.25 * np.sin(2 * np.pi * 220 * t)  # ~ -15 dBFS
    quiet_bg = 0.015 * np.sin(2 * np.pi * 220 * t)  # ~ -39 dBFS
    combined = np.concatenate([loud_main, quiet_bg])

    intervals = [SpeechInterval(0.0, 1.0), SpeechInterval(1.0, 2.0)]
    ref_db = estimate_main_speaker_dbfs(combined, sr, intervals)
    bg_db = measure_interval_dbfs(combined, sr, 1.0, 2.0)
    assert bg_db < ref_db - 13.0


def test_behind_camera_prompter_rejection() -> None:
    import numpy as np
    from reelcut.speaker import (
        estimate_primary_speaker_profile,
        filter_primary_speaker_intervals,
    )
    from reelcut.vad import SpeechInterval

    sr = 16000
    t = np.linspace(0, 1.2, int(1.2 * sr), endpoint=False, dtype=np.float32)
    # Main male creator at ~115 Hz with harmonics, louder
    main_voice = 0.25 * (np.sin(2 * np.pi * 115 * t) + 0.5 * np.sin(2 * np.pi * 230 * t))
    # Behind-camera female prompter at ~225 Hz, slightly quieter (-7 dB)
    prompter_voice = 0.08 * (np.sin(2 * np.pi * 225 * t) + 0.4 * np.sin(2 * np.pi * 450 * t))
    combined = np.concatenate([main_voice, prompter_voice])

    ivs = [SpeechInterval(0.0, 1.2), SpeechInterval(1.2, 2.4)]
    profile = estimate_primary_speaker_profile([(combined, sr, ivs)])
    assert 100.0 <= profile.ref_f0 <= 135.0

    kept = filter_primary_speaker_intervals(combined, sr, ivs, profile)
    assert len(kept) == 1
    assert kept[0].start == 0.0


def test_visual_motion_score_rejects_still_listening_face() -> None:
    import numpy as np
    from reelcut.speaker import SpeakerProfile, is_primary_speaker_segment

    sr = 16000
    t = np.linspace(0, 1.0, sr, endpoint=False, dtype=np.float32)
    voice = 0.12 * np.sin(2 * np.pi * 120 * t)
    profile = SpeakerProfile(ref_db=-24.0, ref_f0=120.0, ref_spec=np.zeros(16, dtype=np.float32))

    # Same-ish pitch but quieter (-4 dB) and motionless face -> rejected
    assert not is_primary_speaker_segment(
        voice, sr, seg_db=-28.0, profile=profile, motion_score=0.05, min_motion_threshold=0.35
    )
    # Active talking face -> accepted
    assert is_primary_speaker_segment(
        voice, sr, seg_db=-28.0, profile=profile, motion_score=1.8, min_motion_threshold=0.35
    )


def test_hybrid_pack_creates_audio_and_thumbnails(tmp_path: Path) -> None:
    from reelcut.hybrid import _save_midpoint_thumbnail, prepare_hybrid_pack

    proj = tmp_path / "hybrid_proj"
    clips_dir = proj / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    clip1 = clips_dir / "01.mp4"
    _make_synthetic_clip(clip1, duration=2.5)
    (proj / "script.txt").write_text("تجربة سكريبت", encoding="utf-8")

    thumb = tmp_path / "thumb.jpg"
    assert _save_midpoint_thumbnail(clip1, 1.0, thumb)
    assert thumb.is_file() and thumb.stat().st_size > 500

    res = prepare_hybrid_pack(proj, include_thumbnails=True)
    assert res["mode"] == "hybrid_ai_pack"
    assert res["has_script"] is True
    assert (proj / "work" / "hybrid_map.json").is_file()
    assert (proj / "work" / "words_detail.json").is_file()
