"""Offline tests for the Gemini voiceover pass and AI B-roll generation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from reelcut import broll_ai
from reelcut.transcribe_gemini import (MIN_SIMILARITY, align, normalize_ar,
                                       split_lines)


# --------------------------------------------------------------- voiceover pass
def test_normalize_unifies_dialect_spellings() -> None:
    a = normalize_ar("الإزازة اللي إنت لابسه دلوقتي؟")
    b = normalize_ar("الازازه اللي انت لابسه دلوقتي")
    assert a == b


def test_split_lines_keeps_arabic_punctuation() -> None:
    lines = split_lines("جملة أولى. جملة تانية؟ Stay safe.")
    assert lines == ["جملة أولى.", "جملة تانية؟", "Stay safe."]


def _win(start: float, end: float, text: str) -> dict:
    return {"start": start, "end": end, "text": text}


def test_align_maps_lines_to_windows_in_order() -> None:
    windows = [
        _win(1.0, 3.0, "الإزازة اللي انت لسه شاربها ممكن تكون البوكسر"),
        _win(4.0, 6.0, "ممكن تكون البوكسر اللي انت لابسه دلوقتي"),
        _win(8.0, 9.0, "ماشي ثانية واحدة"),
        _win(10.0, 12.0, "في التسعينات في دكتور مصري اسمه احمد شفيق"),
    ]
    lines = ["الإزازة اللي إنت لسه شاربها ممكن تكون البوكسر اللي إنت لابسه دلوقتي",
             "في التسعينات في دكتور مصري اسمه أحمد شفيق"]
    segments, dropped = align(lines, windows)
    assert [s["text"] for s in segments] == lines
    # the first line is split by a pause -> spans two windows
    assert (segments[0]["start"], segments[0]["end"]) == (1.0, 6.0)
    assert (segments[1]["start"], segments[1]["end"]) == (10.0, 12.0)
    # chatter that is not in the clean script is reported, not silently kept
    assert [d["text"] for d in dropped] == ["ماشي ثانية واحدة"]


def test_align_flags_unmatched_lines_instead_of_dropping_them() -> None:
    windows = [_win(0.0, 2.0, "كلام مختلف خالص")]
    segments, _dropped = align(["جملة لا توجد في الصوت"], windows)
    assert segments[0]["start"] is None
    assert segments[0]["unmatched"] is True
    assert segments[0]["score"] < MIN_SIMILARITY


# -------------------------------------------------------------------- b-rolls
PLAN = {
    "segments": [],
    "broll_suggestions": [
        {"segment_index": 1, "suggested_filename": "01_hook", "duration_sec": 1.0,
         "prompt": "Vertical 9:16 incense smoke in sunlight"},
        {"segment_index": 3, "suggested_filename": "03_lab.mp4",
         "duration_sec": 30, "prompt": "  lab chambers, two glass tubes  "},
        {"segment_index": 4, "suggested_filename": "04_empty.mp4",
         "duration_sec": 3, "prompt": "   "},
    ],
}


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "edit_plan.json").write_text(
        json.dumps(PLAN, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def test_plan_prompts_fixes_names_clamps_and_skips_empty(project: Path) -> None:
    specs = broll_ai.plan_prompts(project)
    assert [s["name"] for s in specs] == ["01_hook.mp4", "03_lab.mp4"]
    assert specs[0]["duration"] == broll_ai.MIN_SEC          # 1 s -> 2 s
    assert specs[1]["duration"] == broll_ai.MAX_SEC          # 30 s -> 8 s
    assert specs[1]["prompt"].startswith("lab chambers")     # whitespace normalised
    assert [s["segment_index"] for s in specs] == [1, 3]


def test_estimate_counts_images_and_usd(project: Path) -> None:
    specs = broll_ai.plan_prompts(project)
    est = broll_ai.estimate(specs, "gemini_image", "gemini-2.5-flash-image")
    assert est["images"] == 2
    assert est["usd"] == pytest.approx(0.039 * 2, abs=0.001)
    manual = broll_ai.estimate(specs, "manual")
    assert manual["images"] == 0 and manual["usd"] == 0


def test_generate_refuses_without_confirm(project: Path) -> None:
    out = broll_ai.generate_brolls(project, confirm=False)
    assert out["status"] == "needs_confirmation"
    assert out["estimate"]["images"] == 2
    assert not (project / "brolls").exists()


def test_generate_budget_and_provider_guards(project: Path) -> None:
    assert broll_ai.generate_brolls(project, confirm=True, budget_usd=0.01,
                                    )["status"] == "budget_exceeded"
    assert broll_ai.generate_brolls(project, provider="nope",
                                    confirm=True)["status"] == "bad_provider"
    manual = broll_ai.generate_brolls(project, provider="manual", confirm=True)
    assert manual["status"] == "manual_only"
    assert manual["files"] == ["01_hook.mp4", "03_lab.mp4"]


def test_ken_burns_makes_a_real_clip(tmp_path: Path) -> None:
    from PIL import Image

    img = tmp_path / "still.png"
    Image.new("RGB", (1080, 1920), (20, 30, 40)).save(img)
    out = broll_ai.ken_burns(img, tmp_path / "clip.mp4", 2.0)
    assert out.is_file() and out.stat().st_size > 5000
    from reelcut.media import probe_media

    assert probe_media(out).duration == pytest.approx(2.0, abs=0.35)


# --------------------------------------------------------------- flow video path
def test_estimate_credits_for_flow_video(project: Path) -> None:
    specs = broll_ai.plan_prompts(project)
    est = broll_ai.estimate(specs, "flow_video")
    # 2 s -> 3 s min = 9 credits, 8 s = 24 credits
    assert est["credits"] == 9 + 24
    assert est["usd"] == 0


def test_flow_video_reports_not_configured(project: Path, monkeypatch) -> None:
    from reelcut import broll_flow

    monkeypatch.setattr(broll_flow, "available",
                        lambda: {"configured": False, "chrome": False,
                                 "driver": True, "project": "",
                                 "hint": "start Chrome"})
    out = broll_ai.generate_brolls(project, provider="flow_video", confirm=True)
    assert out["status"] == "not_configured"
    assert out["hint"] == "start Chrome"


def test_flow_video_quotes_before_spending(project: Path, monkeypatch) -> None:
    from reelcut import broll_flow

    monkeypatch.setattr(broll_flow, "available",
                        lambda: {"configured": True, "chrome": True,
                                 "driver": True, "project": "uuid", "hint": ""})
    out = broll_ai.generate_brolls(project, provider="flow_video")
    assert out["status"] == "needs_confirmation"
    assert out["estimate"]["credits"] == 33


def test_flow_video_budget_and_happy_path(project: Path, monkeypatch) -> None:
    import subprocess

    from reelcut import broll_flow
    from reelcut.media import get_ffmpeg_exe

    monkeypatch.setattr(broll_flow, "available",
                        lambda: {"configured": True, "chrome": True,
                                 "driver": True, "project": "uuid", "hint": ""})
    low = broll_ai.generate_brolls(project, provider="flow_video",
                                   confirm=True, budget_credits=10)
    assert low["status"] == "budget_exceeded"

    def fake_generate(specs, out_dir, project=None, job_path=None, log=print):
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        for s in specs:
            subprocess.run([get_ffmpeg_exe(), "-y", "-v", "error", "-f",
                            "lavfi", "-i", "color=c=navy:s=160x284:d=1",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p",
                            str(out / s["name"])], check=True,
                           capture_output=True)
        return {"status": "done", "finished": len(specs), "total": len(specs)}

    monkeypatch.setattr(broll_flow, "generate", fake_generate)
    out = broll_ai.generate_brolls(project, provider="flow_video",
                                   confirm=True, budget_credits=60)
    assert out["status"] == "done"
    ids = {a["broll_id"] for a in out["attachments"]}
    assert ids == {"b001", "b002"}
