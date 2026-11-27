"""First-run helpers: friendly errors, env key handling, status wording."""
from __future__ import annotations

from pathlib import Path

import pytest

from reelcut import firstrun


def test_explain_translates_common_failures_into_next_steps() -> None:
    cases = {
        ModuleNotFoundError("No module named 'playwright'"): "install.bat",
        RuntimeError("Chrome did not open its debug port on 9223"): "Chrome",
        RuntimeError("no prompt box on the project page"): "reelcut setup",
        RuntimeError("GEMINI_API_KEY is not set"): "aistudio",
        ValueError("something odd"): "reelcut setup --status",
    }
    for exc, expected in cases.items():
        text = firstrun._explain(exc)
        assert expected in text
        # always bilingual: Arabic line + English hint
        assert "\n" in text


def test_env_key_detection_and_write(tmp_path: Path, monkeypatch) -> None:
    env = tmp_path / ".env"
    monkeypatch.setattr(firstrun, "ENV", env)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert firstrun._env_has("GEMINI_API_KEY") is False
    firstrun._write_env("GEMINI_API_KEY", "abc123")
    assert env.read_text(encoding="utf-8").strip() == "GEMINI_API_KEY=abc123"
    assert firstrun._env_has("GEMINI_API_KEY") is True
    # rewriting replaces instead of duplicating
    firstrun._write_env("GEMINI_API_KEY", "def456")
    body = env.read_text(encoding="utf-8")
    assert body.count("GEMINI_API_KEY=") == 1
    assert "def456" in body


def test_credits_text_reads_the_common_shapes() -> None:
    assert firstrun._credits_text({"credits_num": 974}) == "974"
    assert firstrun._credits_text({"credits_text": "1,026"}) == "1,026"
    assert "غير مقروء" in firstrun._credits_text({})
    assert firstrun._credits_text(120) == "120"


def test_status_reports_missing_pieces(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(firstrun, "ENV", tmp_path / ".env")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    from reelcut import broll_flow

    monkeypatch.setattr(broll_flow, "available",
                        lambda: {"configured": False, "chrome": False,
                                 "driver": True, "project": "",
                                 "hint": "start Chrome"})
    st = firstrun.status()
    assert st["gemini_key"] is False
    assert st["flow"]["configured"] is False
    assert st["ready"] is False


def test_status_ready_when_key_and_flow_exist(tmp_path: Path, monkeypatch) -> None:
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=x\n", encoding="utf-8")
    monkeypatch.setattr(firstrun, "ENV", env)
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    from reelcut import broll_flow

    monkeypatch.setattr(broll_flow, "available",
                        lambda: {"configured": True, "chrome": True,
                                 "driver": True, "project": "uuid",
                                 "hint": ""})
    st = firstrun.status()
    assert st["ready"] is True
    assert st["project"] == "uuid"
