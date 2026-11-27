"""MCP server exposing token-efficient local video editing tools for AI agents."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from .brolls import fetch_stock_brolls, index_local_brolls
from .broll_ai import estimate as broll_estimate
from .broll_ai import generate_brolls as _generate_brolls
from .broll_ai import plan_prompts as broll_plan_prompts
from .broll_ai import status as broll_status
from .hybrid import prepare_hybrid_pack as _prepare_hybrid_pack
from .models import EditPlan
from .planner import save_edit_plan
from .renderer import render_video as _render_video
from .transcribe import prepare_project as _prepare_project
from .transcribe_gemini import transcribe_voiceover as _transcribe_voiceover
from .transcribe_gemini import write_transcript as _write_transcript

mcp = MCPServer(
    "reelcut",
    instructions=(
        "Token-efficient local 9:16 Arabic video editing server. "
        "For Arabic voiceover recordings with retakes, prefer transcribe_voiceover: it "
        "extracts the final clean script (Gemini) and returns segments whose start/end "
        "seconds come from local VAD, so cuts never land mid-word. "
        "Always read the transcript & script, save the plan via save_edit_plan, show the "
        "2-line Arabic summary to the user for approval, and ONLY call render_video after "
        "the user approves. B-rolls: if the plan has suggestions, generate_brolls can make "
        "them with Gemini images (9:16 stills + camera move) after quoting the cost and "
        "receiving confirm=True; otherwise ask the creator to drop files in brolls/."
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


@mcp.tool()
def transcribe_voiceover(project_dir: str, audio: str | None = None,
                         model: str | None = None) -> dict[str, Any]:
    """Gemini voiceover transcription with exact cut-safe timings.

    Pass A (whole recording) applies the voiceover-editor system instruction:
    keep only the final take of each line, drop stumbles/false starts/breaths and
    off-script chatter, stitch pickups, preserve the dialect, return a clean script.
    Pass B (local Silero VAD) finds speech windows and aligns those script lines
    onto them, so `segments[i].start/end` are real audio seconds that always fall
    in silence — safe to cut without touching the result.

    Writes work/gemini_transcript.json. Requires GEMINI_API_KEY.
    `audio` defaults to the single file in clips/ (or the project root).
    """
    import json as _json

    from .transcribe_gemini import _pcm16

    proj = Path(project_dir).resolve()
    if audio:
        target = Path(audio)
    else:
        candidates = [p for p in (proj / "clips").glob("*")
                      if p.suffix.lower() in {".mp4", ".mov", ".mkv", ".wav",
                                              ".m4a", ".mp3", ".webm"}] or \
                     [p for p in proj.glob("*") if p.suffix.lower() in
                      {".mp4", ".mov", ".mkv", ".wav", ".m4a", ".mp3"}]
        if not candidates:
            return {"status": "no_audio",
                    "hint": "Put the recording in clips/ or pass audio=..."}
        target = sorted(candidates)[0]
    res = _transcribe_voiceover(target, model=model or "gemini-3.5-flash")
    path = _write_transcript(proj, res)
    return {"status": "ok", "transcript_path": str(path),
            "audio": str(target), "model": res["model"],
            "speech_seconds": res["speech_seconds"], "windows": res["windows"],
            "segments": res["segments"], "dropped": res["dropped"],
            "script": res["script"],
            "hint": "Build EditPlan.segments from these start/end/text values."}


@mcp.tool()
def generate_brolls(project_dir: str, provider: str = "gemini_image",
                    confirm: bool = False, max_clips: int | None = None,
                    budget_usd: float | None = None) -> dict[str, Any]:
    """Generate the plan's B-rolls as 9:16 clips (Gemini images + camera move).

    Reads work/edit_plan.json -> broll_suggestions, writes clips into brolls/
    using the suggested filenames, indexes them and returns
    `attachments` (segment_index -> broll_id) for save_edit_plan.

    Always quotes first: without confirm=True it returns the image count and
    estimated USD. `budget_usd` is a hard ceiling. Providers: gemini_image
    (default, no browser), manual (prints the filenames), stock/flow_video
    (gemini_image/mannual need no browser; flow_video drives the automation\n    Chrome (REELCUT_FLOW_PROJECT) and costs ~3 credits/second).
    """
    return _generate_brolls(project_dir=project_dir, provider=provider,
                            confirm=confirm, max_clips=max_clips,
                            budget_usd=budget_usd)


@mcp.tool()
def broll_status(project_dir: str) -> dict[str, Any]:
    """Progress of the last B-roll batch (job file) + clips already on disk."""
    return broll_status(project_dir)


@mcp.tool()
def quote_brolls(project_dir: str, provider: str = "gemini_image") -> dict[str, Any]:
    """Free quote for the plan's B-rolls: prompts, filenames and estimated USD."""
    specs = broll_plan_prompts(project_dir)
    return {"clips": len(specs), "files": [s["name"] for s in specs],
            "durations": [s["duration"] for s in specs],
            "estimate": broll_estimate(specs, provider=provider)}




@mcp.tool()
def flow_login(project: str | None = None, wait_seconds: int = 0) -> dict[str, Any]:
    """One-time Flow setup for the flow_video provider (no credits spent).

    Opens the automation Chrome on flow.google.com. If nobody is signed in it
    returns `status="sign_in_required"`: ask the creator to sign in once in that
    window, then call again (or pass wait_seconds to poll). Once signed in it
    returns the account, the credit balance, the visible projects and saves the
    chosen `project` uuid to .env as REELCUT_FLOW_PROJECT.
    """
    from .broll_flow import login as _flow_login

    return _flow_login(project=project, wait_seconds=wait_seconds)



@mcp.tool()
def setup_status() -> dict[str, Any]:
    """Plain-language setup state: Gemini key, Flow session, credits, project.

    Offline and free. Use it before the first paid call to tell the creator
    exactly what is missing (and to point them at `reelcut setup`).
    """
    from .firstrun import status as _status

    return _status()

def main() -> None:
    """Run ReelCut MCP server over stdio."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()



