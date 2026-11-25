"""MCP server exposing token-efficient local video editing tools for AI agents."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from .brolls import fetch_stock_brolls, index_local_brolls
from .hybrid import prepare_hybrid_pack as _prepare_hybrid_pack
from .models import EditPlan
from .planner import save_edit_plan
from .renderer import render_video as _render_video
from .transcribe import prepare_project as _prepare_project

mcp = MCPServer(
    "reelcut",
    instructions=(
        "Token-efficient local 9:16 Arabic video editing server. "
        "Always run prepare_project first, read the compact transcript & script, "
        "save the plan via save_edit_plan, show the 2-line Arabic summary to the user "
        "for approval, and ONLY call render_video after the user approves."
    ),
)


@mcp.tool()
def prepare_project(
    project_dir: str,
    whisper_model: str = "small",
    auto_trim_silence: bool = True,
    brolls_dir: str | None = None,
) -> dict[str, Any]:
    """Enhance audio, trim silence (Silero VAD), transcribe clips (Faster-Whisper int8),
    and return a compact JSON summary with script_text and local B-roll tags.
    """
    return _prepare_project(
        project_dir=project_dir,
        whisper_model=whisper_model,
        auto_trim_silence=auto_trim_silence,
        brolls_dir=brolls_dir,
    )


@mcp.tool()
def prepare_hybrid_pack(
    project_dir: str,
    brolls_dir: str | None = None,
    include_thumbnails: bool = True,
) -> dict[str, Any]:
    """Skip slow CPU Whisper and pack main-speaker VAD speech into one compact MP3
    (`work/hybrid_speech.mp3`) + 360p midpoint thumbnails + `work/hybrid_map.json`.
    Ideal when a multimodal AI inspects audio/thumbnails directly in 10-20s.
    """
    return _prepare_hybrid_pack(
        project_dir=project_dir,
        brolls_dir=brolls_dir,
        include_thumbnails=include_thumbnails,
    )


@mcp.tool()
def index_brolls(brolls_dir: str) -> dict[str, Any]:
    """Index a local B-roll folder once and return compact broll_id/tags/duration list."""
    items = index_local_brolls(brolls_dir)
    compact = [
        {"broll_id": b["broll_id"], "tags": b.get("tags", []), "duration": b.get("duration", 0.0)}
        for b in items
    ]
    return {"count": len(compact), "brolls": compact}


@mcp.tool()
def fetch_brolls(queries: list[str], project_dir: str) -> dict[str, Any]:
    """Download portrait stock B-rolls from Pexels/Pixabay into project local cache."""
    cache_dir = Path(project_dir).resolve() / "work" / "brolls_cache"
    return fetch_stock_brolls(queries=queries, cache_dir=cache_dir, project_dir=project_dir)


@mcp.tool()
def save_edit_plan(project_dir: str, plan: dict[str, Any]) -> dict[str, Any]:
    """Validate and save the AI director EditPlan, returning a 2-line Arabic summary.

    Each item in `plan["segments"]` must include:
      - clip_id: str (e.g. "c01")
      - start: float (seconds)
      - end: float (seconds)
      - corrected_text: str (Colloquial Arabic fixed using script.txt)
      - zoom: bool (optional 1.12x punch-in on occasional cuts)
      - broll: optional {broll_id, path, query, offset_sec, duration_sec}
    """
    validated = EditPlan.model_validate(plan)
    return save_edit_plan_impl(project_dir=project_dir, plan_data=validated)


def save_edit_plan_impl(project_dir: str, plan_data: EditPlan) -> dict[str, Any]:
    return save_edit_plan(project_dir=project_dir, plan_data=plan_data)


@mcp.tool()
def render_video(project_dir: str) -> dict[str, Any]:
    """Render output/final_reel.mp4 (1080x1920, enhanced audio, B-rolls, RTL captions)."""
    return _render_video(project_dir=project_dir)


def main() -> None:
    """Run ReelCut MCP server over stdio."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
