# AGENTS.md

Instructions for AI assistants and coding agents working with or operating **ReelCut MCP**.

## Purpose
ReelCut is a **token-efficient, CPU-friendly local video editing pipeline and MCP server** built for turning raw talking-head clips (`9:16` social reels, optimized for colloquial Arabic) into a finished MP4 with mastered audio, subtle zoom cuts, B-rolls, source QR overlays, and dynamic 2–4 word RTL captions.

## Golden Token-Efficiency Rule
- **Never read, inspect, or send raw video/audio files or frames to an LLM.**
- All heavy media tasks (audio cleanup, `-14 LUFS` normalization, Silero VAD, speaker verification, Whisper `int8` transcription, Pillow RTL caption rendering, and FFmpeg compositing) run **locally at 0 LLM tokens**.
- The AI acts purely as **Director / Planner**, reading compact JSON (`work/transcript_compact.json`) and returning a compact `EditPlan` JSON.

## Architecture Map
- [pyproject.toml](pyproject.toml) — Package definition, dependencies, CLI (`reelcut`) & MCP (`reelcut-mcp`) entry points.
- [src/reelcut/config.py](src/reelcut/config.py) — Environment settings (`.env`), Windows Arabic font discovery, `CaptionStyle`.
- [src/reelcut/media.py](src/reelcut/media.py) — FFmpeg/FFprobe discovery (`imageio-ffmpeg` fallback), audio extraction, adaptive gating (`agate`) + `-14 LUFS` mastering.
- [src/reelcut/vad.py](src/reelcut/vad.py) — Silero VAD ONNX (`faster_whisper.vad`) + RMS loudness measurement (`dBFS`).
- [src/reelcut/speaker.py](src/reelcut/speaker.py) — Primary (on-camera) speaker voiceprint verification (`dBFS` + `F0` pitch + spectral timbre) to reject behind-camera prompters and bystanders.
- [src/reelcut/transcribe.py](src/reelcut/transcribe.py) — `prepare_project()`: pure-Arabic prompt generation from script, `faster-whisper` (`int8`) transcription, turn splitting, and compact JSON export.
- [src/reelcut/brolls.py](src/reelcut/brolls.py) — Local B-roll library indexing (`brolls_index.json`) and optional Pexels/Pixabay portrait fetching.
- [src/reelcut/models.py](src/reelcut/models.py) — Pydantic contracts (`EditPlan`, `PlannedSegment`, `BRollPlacement`, etc.).
- [src/reelcut/planner.py](src/reelcut/planner.py) — `save_edit_plan()`: validates `EditPlan`, aligns corrected colloquial words to timestamps, generates `summary_2_lines`.
- [src/reelcut/captions.py](src/reelcut/captions.py) — Renders 2–4 word highlighted Arabic RTL PNG overlays (`Pillow` + `arabic-reshaper` + `python-bidi`) and `qr_url` corner badges.
- [src/reelcut/renderer.py](src/reelcut/renderer.py) — `render_video()`: trims segments, applies `1.12x` zoom cuts, composites B-rolls, captions, and QR badges into `output/final_reel.mp4`.
- [src/reelcut/mcp_server.py](src/reelcut/mcp_server.py) — `MCPServer` (`mcp >= 2.0`) stdio server exposing 5 tools.
- [src/reelcut/cli.py](src/reelcut/cli.py) — Typer CLI mirror of all pipeline stages.

## Standard AI Director Workflow
When a user asks to edit a project folder (containing raw clips in `clips/` or root, and optional `script.txt` or `script_resources.txt`):

1. **Prepare locally (`prepare_project`)**:
   - Run `prepare_project(project_dir=...)`.
   - Read the compact response: `script_text`, `segments` (`id`, `clip_id`, `start`, `end`, `text`), and `local_brolls`.
2. **Build `EditPlan`**:
   - Keep only clean takes matching the script order; drop stumbles, retakes, and any speech not belonging to the script/creator.
   - Fix colloquial Arabic spelling in `corrected_text` using `script_text`.
   - Never include raw URLs from `script_resources.txt` inside spoken `corrected_text`; instead exclude them or attach `qr_url` (with `qr_label="المصدر"`) on the relevant segment to show a small top-right QR code.
   - Set `zoom: true` on ~20–35% of cuts for subtle `1.12x` visual pacing.
   - Attach B-rolls using `broll_id` from `local_brolls` (or call `fetch_brolls` if stock API keys are configured).
3. **Approval Gate (`save_edit_plan`)**:
   - Call `save_edit_plan(project_dir=..., plan=...)`.
   - Present the returned `summary_2_lines` in Arabic to the user and **wait for explicit approval** before rendering.
4. **Render (`render_video`)**:
   - After user approval, call `render_video(project_dir=...)` and report `output/final_reel.mp4`, duration, resolution, and size.

## Development & Verification
- Environment: Python `>=3.11` (Windows CPU-only supported out of the box; FFmpeg bundled via `imageio-ffmpeg` if missing from `PATH`).
- Run tests after any code edit:
  ```powershell
  .venv\Scripts\python.exe -m pytest tests -v
  ```
- See [docs/MCP_TOOLS.md](docs/MCP_TOOLS.md) for full tool schemas and [examples/sample_plan.json](examples/sample_plan.json) for a valid `EditPlan`.
