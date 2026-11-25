# ReelCut MCP

Token-efficient, open-source **9:16 Arabic video editing pipeline + MCP server** designed for **Windows (CPU-only)** and AI assistants (Antigravity, Claude, Gemini, Codex, Cursor).

## Why ReelCut?
- **0-Token Local Media Processing:** Audio enhancement & `-14 LUFS` mastering (`FFmpeg`), voice activity detection (`Silero VAD` ONNX), and `int8` CPU transcription (`Faster-Whisper`).
- **Primary-Speaker Isolation (`speaker.py`):** Uses loudness (`dBFS`), fundamental pitch (`F0`), and spectral timbre to reject background chatter and behind-camera prompters even when they read the same script.
- **Minimal LLM Tokens:** The AI reads only compact JSON (`work/transcript_compact.json`), `script.txt` / `script_resources.txt`, and `brolls_index.json` — never raw video/audio.
- **2-Line Approval Gate:** Before slow CPU rendering starts, the AI presents a concise 2-line Arabic plan summary for confirmation.
- **Dynamic Arabic RTL Captions & Source QR Codes:** Renders 2–4 word highlighted Arabic captions (`Pillow` + `arabic-reshaper` + `python-bidi`) and optional corner QR badges for citations.

## Quick Setup (Windows)

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
copy .env.example .env
```

> FFmpeg is automatically provided via `imageio-ffmpeg` if `ffmpeg.exe` is not on your `PATH`.

### Connect as an MCP Server
Copy `mcp_config.example.json` into your client's `mcp_config.json` (e.g. `~/.gemini/config/mcp_config.json`) and update the path to `.venv\Scripts\python.exe`.

### Verify Installation
```powershell
.venv\Scripts\python.exe -m pytest tests -v
```

## Video Project Folder Layout
```text
my_video_project/
├── clips/                # Raw talking-head clips (or place directly in project root)
├── script.txt            # Optional written Arabic script (or script_resources.txt with URLs)
├── brolls/               # Optional project B-rolls
├── work/                 # Intermediate audio, transcripts, edit_plan.json
└── output/
    └── final_reel.mp4    # Rendered 1080x1920 MP4
```

## CLI Usage
```powershell
.venv\Scripts\reelcut.exe prepare path\to\project
.venv\Scripts\reelcut.exe index-brolls path\to\brolls
.venv\Scripts\reelcut.exe apply-plan path\to\project path\to\plan.json
.venv\Scripts\reelcut.exe render path\to\project
```

## Documentation for AI Tools
- [AGENTS.md](AGENTS.md) — Universal instructions for AI coding and desktop agents.
- [GEMINI.md](GEMINI.md) & [CLAUDE.md](CLAUDE.md) — Agent-specific entry instructions.
- [.agents/skills/reelcut/SKILL.md](.agents/skills/reelcut/SKILL.md) — Bundled Antigravity/Agent skill.
- [docs/MCP_TOOLS.md](docs/MCP_TOOLS.md) — MCP tools & `EditPlan` schema reference.
- [llms.txt](llms.txt) — Machine-readable project map.
