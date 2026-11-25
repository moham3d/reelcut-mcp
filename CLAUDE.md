# CLAUDE.md

Read and follow [AGENTS.md](AGENTS.md) before modifying code or running the ReelCut pipeline.

## Quick Rules
- Never inspect raw video/audio files with multimodal tokens; run `prepare_project` (MCP) or `reelcut prepare` (CLI) first.
- Always use `save_edit_plan` and wait for user confirmation of `summary_2_lines` before calling `render_video`.
- After edits, run `.venv\Scripts\python.exe -m pytest tests -v` to confirm all tests pass.
- See [docs/MCP_TOOLS.md](docs/MCP_TOOLS.md) for exact MCP tool signatures and `EditPlan` schema.
