"""Typer CLI entry point for ReelCut local video editing."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from .brolls import fetch_stock_brolls, index_local_brolls
from .hybrid import prepare_hybrid_pack
from .planner import save_edit_plan
from .renderer import render_video
from .transcribe import prepare_project

app = typer.Typer(
    name="reelcut",
    help="Token-efficient local 9:16 Arabic video editing pipeline (CPU-friendly).",
    no_args_is_help=True,
)


@app.command()
def prepare(
    project_dir: Path = typer.Argument(..., help="Project directory with clips/ and optional script.txt"),
    whisper_model: str = typer.Option("small", "--model", "-m", help="Faster-Whisper CPU model"),
    no_trim: bool = typer.Option(False, "--no-trim", help="Disable Silero VAD silence trimming"),
    brolls_dir: Path | None = typer.Option(None, "--brolls", "-b", help="Optional shared B-rolls folder"),
) -> None:
    """Enhance audio, trim silence, transcribe clips, and write compact JSON."""
    res = prepare_project(
        project_dir=project_dir,
        whisper_model=whisper_model,
        auto_trim_silence=not no_trim,
        brolls_dir=brolls_dir,
    )
    typer.echo(json.dumps(res, ensure_ascii=False, indent=2))


@app.command("prepare-hybrid")
def prepare_hybrid(
    project_dir: Path = typer.Argument(..., help="Project directory with clips/ and optional script"),
    brolls_dir: Path | None = typer.Option(None, "--brolls", "-b", help="Optional shared B-rolls folder"),
    no_thumbs: bool = typer.Option(False, "--no-thumbs", help="Skip 360p midpoint thumbnail extraction"),
) -> None:
    """Pack VAD-filtered main-speaker speech MP3 + 360p thumbnails for fast multimodal AI planning."""
    res = prepare_hybrid_pack(
        project_dir=project_dir,
        brolls_dir=brolls_dir,
        include_thumbnails=not no_thumbs,
    )
    typer.echo(json.dumps(res, ensure_ascii=False, indent=2))


@app.command("index-brolls")
def index_brolls_cmd(
    brolls_dir: Path = typer.Argument(..., help="Directory containing B-roll videos/images"),
) -> None:
    """Index a local B-roll library into brolls_index.json."""
    items = index_local_brolls(brolls_dir)
    typer.echo(json.dumps({"count": len(items), "items": items}, ensure_ascii=False, indent=2))


@app.command("fetch-brolls")
def fetch_brolls_cmd(
    project_dir: Path = typer.Argument(..., help="Project directory"),
    queries: list[str] = typer.Argument(..., help="English search queries for Pexels/Pixabay"),
) -> None:
    """Fetch vertical stock B-rolls into project cache (requires API key in .env)."""
    cache_dir = Path(project_dir).resolve() / "work" / "brolls_cache"
    res = fetch_stock_brolls(queries=queries, cache_dir=cache_dir, project_dir=project_dir)
    typer.echo(json.dumps(res, ensure_ascii=False, indent=2))


@app.command("apply-plan")
def apply_plan_cmd(
    project_dir: Path = typer.Argument(..., help="Project directory"),
    plan_file: Path = typer.Argument(..., help="JSON file matching EditPlan schema"),
) -> None:
    """Validate and save an EditPlan JSON, printing the 2-line Arabic summary."""
    plan_data = json.loads(plan_file.read_text(encoding="utf-8"))
    res = save_edit_plan(project_dir=project_dir, plan_data=plan_data)
    typer.echo(json.dumps(res, ensure_ascii=False, indent=2))


@app.command()
def render(
    project_dir: Path = typer.Argument(..., help="Project directory with saved edit plan"),
) -> None:
    """Render final 9:16 MP4 with B-rolls and dynamic Arabic captions."""
    res = render_video(project_dir=project_dir)
    typer.echo(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    app()



