"""Tests for CLI help, MCP server tool registration, and plan validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from reelcut.cli import app
from reelcut.mcp_server import mcp
from reelcut.models import EditPlan


def test_cli_help() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("prepare", "prepare-hybrid", "index-brolls", "fetch-brolls", "apply-plan", "render"):
        assert cmd in result.stdout


def test_mcp_tools_registered() -> None:
    tool_names = {t.name for t in mcp._tool_manager.list_tools()}
    assert {
        "prepare_project",
        "prepare_hybrid_pack",
        "index_brolls",
        "fetch_brolls",
        "save_edit_plan",
        "render_video",
    }.issubset(tool_names)


def test_edit_plan_validation_rejects_empty() -> None:
    with pytest.raises(ValidationError):
        EditPlan.model_validate({"segments": []})
