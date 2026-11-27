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


@app.command("transcribe-gemini")
def transcribe_gemini_cmd(
    project_dir: Path = typer.Argument(..., help="Project directory"),
    audio: Path | None = typer.Option(None, "--audio", "-a",
                                      help="Recording file (default: the file in clips/)"),
    model: str = typer.Option("gemini-3.5-flash", "--model", "-m"),
) -> None:
    """Clean voiceover script + cut-safe timings (Gemini text, local VAD time)."""
    from .transcribe_gemini import transcribe_voiceover, write_transcript

    target = audio
    if target is None:
        found = sorted((project_dir / "clips").glob("*")) or sorted(project_dir.glob("*.mp4"))
        if not found:
            raise typer.BadParameter("no recording found in clips/ (pass --audio)")
        target = found[0]
    res = transcribe_voiceover(target, model=model)
    path = write_transcript(project_dir, res)
    typer.echo(f"speech {res['speech_seconds']}s | {res['windows']} windows | "
               f"{len(res['dropped'])} dropped | {path}")
    for seg in res["segments"]:
        when = (f"{seg['start']:6.2f}-{seg['end']:6.2f}"
                if seg.get("start") is not None else "   ?  -   ? ")
        typer.echo(f"[{when}] {seg['text']}")


@app.command("brolls")
def brolls_cmd(
    project_dir: Path = typer.Argument(..., help="Project directory with a saved plan"),
    provider: str = typer.Option("gemini_image", "--provider", "-p",
                                 help="gemini_image | flow_video | manual | stock"),
    confirm: bool = typer.Option(False, "--confirm", help="Actually spend (default: quote only)"),
    max_clips: int | None = typer.Option(None, "--max-clips"),
    budget_usd: float | None = typer.Option(None, "--budget-usd"),
    budget_credits: int | None = typer.Option(None, "--budget-credits"),
) -> None:
    """Quote, then generate the plan's B-rolls as 9:16 clips."""
    from .broll_ai import estimate, generate_brolls, plan_prompts

    specs = plan_prompts(project_dir)
    if not specs:
        typer.echo("no broll_suggestions in work/edit_plan.json")
        raise typer.Exit(2)
    typer.echo(f"clips: {len(specs)} | estimate: {estimate(specs, provider)}")
    if not confirm:
        for s in specs:
            typer.echo(f"  - {s['name']} ({s['duration']}s) {s['prompt'][:80]}")
        typer.echo("quote only - pass --confirm to generate")
        return
    out = generate_brolls(project_dir=project_dir, provider=provider,
                          confirm=True, max_clips=max_clips,
                          budget_usd=budget_usd,
                          budget_credits=budget_credits)
    typer.echo(json.dumps(out, ensure_ascii=False, indent=2))


@app.command("flow-login")
def flow_login_cmd(
    project: str | None = typer.Option(None, "--project", "-p",
                                       help="Flow project uuid to save"),
    wait: int = typer.Option(0, "--wait", help="Seconds to wait for the sign-in"),
    u_index: int = typer.Option(0, "--u", help="Google account index in the profile"),
) -> None:
    """One-time Flow setup: open the automation Chrome, sign in, save the project."""
    from .broll_flow import login

    out = login(project=project, wait_seconds=wait, u_index=u_index)
    typer.echo(json.dumps(out, ensure_ascii=False, indent=2))
    if out.get("status") != "ok":
        raise typer.Exit(3)


@app.command("setup")
def setup_cmd(
    wait: int = typer.Option(180, "--wait", help="Seconds to wait for the Google sign-in"),
    no_key_prompt: bool = typer.Option(False, "--no-key-prompt"),
    no_input: bool = typer.Option(False, "--no-input", help="Non-interactive"),
) -> None:
    """Guided first run: Gemini key, Google sign-in, credits, project."""
    from .firstrun import setup

    raise typer.Exit(setup(wait_seconds=wait, ask_key=not no_key_prompt,
                           interactive=not no_input))


@app.command("status")
def status_cmd() -> None:
    """Show setup status in plain language (no browser, no credits)."""
    from .firstrun import print_status

    print_status()

if __name__ == "__main__":
    app()




