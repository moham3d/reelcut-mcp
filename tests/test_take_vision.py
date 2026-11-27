"""Offline tests for the visual take review (no API, no video decoding)."""
from __future__ import annotations

import pytest

from reelcut import take_vision as tv


def test_parse_verdict_reads_the_one_line_contract() -> None:
    assert tv.parse_verdict("camera|yes|yes|facing lens") == {
        "person": "camera", "eye_contact": True, "usable": True,
        "reason": "facing lens"}
    phone = tv.parse_verdict("phone|no|no|looking at phone")
    assert phone["person"] == "phone" and phone["usable"] is False
    away = tv.parse_verdict("away|no|no|walking")
    assert away["usable"] is False


def test_parse_verdict_tolerates_prose_and_short_lines() -> None:
    raw = "Sure, here it is:\ncamera|yes|yes|presenting\nthanks"
    assert tv.parse_verdict(raw)["usable"] is True
    # two fields only (truncated model output) still parses person + eyes
    short = tv.parse_verdict("camera|yes")
    assert short["person"] == "camera" and short["eye_contact"] is True
    # unknown person never counts as usable
    assert tv.parse_verdict("dog|yes|yes|x")["usable"] is False


def test_usable_flag_requires_camera_and_eye_contact() -> None:
    # model claims usable but says it is looking at the phone
    assert tv.parse_verdict("phone|yes|yes|oops")["usable"] is False
    # model claims usable but no eye contact
    assert tv.parse_verdict("camera|no|yes|oops")["usable"] is False


@pytest.fixture()
def no_frames(monkeypatch) -> None:
    monkeypatch.setattr(tv, "frame_bytes", lambda *_a, **_k: b"jpeg")


def _classify(seq: list[dict]):
    it = iter(seq)
    return lambda *_a, **_k: next(it)


def test_review_take_aggregates_and_suggests_trims(no_frames, monkeypatch) -> None:
    monkeypatch.setattr(tv, "classify_frame", _classify([
        {"person": "phone", "eye_contact": False, "usable": False,
         "reason": "phone"},
        {"person": "camera", "eye_contact": True, "usable": True, "reason": ""},
        {"person": "camera", "eye_contact": True, "usable": True, "reason": ""},
    ]))
    out = tv.review_take("v.mp4", 10.0, 13.0, samples=3)
    assert out["ratio"] == pytest.approx(0.67, abs=0.01)
    assert out["usable"] is True           # 2 of 3 good frames is enough
    assert out["trim_start"] is not None    # drops the leading phone moment
    assert out["trim_end"] is None


def test_review_take_rejects_a_preparation_take(no_frames, monkeypatch) -> None:
    monkeypatch.setattr(tv, "classify_frame", _classify([
        {"person": "phone", "eye_contact": False, "usable": False, "reason": "phone"},
        {"person": "phone", "eye_contact": False, "usable": False, "reason": "phone"},
        {"person": "away", "eye_contact": False, "usable": False, "reason": "walking"},
    ]))
    out = tv.review_take("v.mp4", 0.0, 3.0, samples=3)
    assert out["usable"] is False
    assert out["ratio"] == 0.0
    assert "phone" in out["reason"]


def test_review_take_survives_a_failed_frame(no_frames, monkeypatch) -> None:
    def boom(*_a, **_k):
        raise RuntimeError("vision down")

    monkeypatch.setattr(tv, "classify_frame", boom)
    out = tv.review_take("v.mp4", 0.0, 2.0, samples=2)
    assert out["usable"] is False                 # a blind sample is not a pass
    assert any("vision down" in r["reason"] for r in out["samples"])


def test_review_segments_skips_unmatched_lines(no_frames, monkeypatch) -> None:
    monkeypatch.setattr(tv, "classify_frame", _classify([
        {"person": "camera", "eye_contact": True, "usable": True, "reason": ""},
    ] * 3))
    rows = tv.review_segments("v.mp4", [
        {"start": 1.0, "end": 3.0, "text": "a"},
        {"start": None, "end": None, "text": "b"},
    ], samples=3, log=lambda *_: None)
    assert rows[0]["usable"] is True
    assert rows[1]["skipped"] == "unmatched"
