# GEMINI.md

Follow [AGENTS.md](AGENTS.md) for all architecture details, token-efficiency rules, and editing workflows.

## Key Antigravity Notes
- The workspace skill lives at `.agents/skills/reelcut/SKILL.md`.
- Register the MCP server using `.venv\Scripts\python.exe -m reelcut.mcp_server` (see `mcp_config.example.json`).
- **Always** pause after `save_edit_plan` and show `summary_2_lines` in Arabic before calling `render_video`.
- Verify code changes with `.venv\Scripts\python.exe -m pytest tests -v`.
