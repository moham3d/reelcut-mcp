# ReelCut MCP

Token-efficient, open-source **9:16 Arabic video editing pipeline + MCP server** designed for **Windows & Linux (CPU-only)** and AI assistants (Antigravity, Claude, Gemini, Codex, Cursor).

## Why ReelCut?
- **0-Token Local Media Processing:** Audio enhancement & `-14 LUFS` mastering, voice activity detection (`Silero VAD` ONNX), and `int8` CPU transcription (`Faster-Whisper`).
- **Low-Token Hybrid Mode (`prepare_hybrid_pack`):** Packs only main-speaker speech into one small MP3 + `360p` midpoint thumbnails (~10s prep, skipping slow CPU Whisper) for multimodal AI planning.
- **Frame-Synced Rendering (`FilmCraft` + `FFmpeg`):** Automatically uses `filmcraft-cli` (when available) to build an editable `output/project.fcproj` with linked A/V tracks, `-14 LUFS` Instagram Reels export preset, and optional video color presets (`clean`, `warm`, `cinematic`, `crisp`, `bw`), falling back to locked-source `FFmpeg` when absent.
- **Primary-Speaker Isolation (`speaker.py`):** Uses loudness (`dBFS`), fundamental pitch (`F0`), spectral timbre, and center-frame face motion to reject background chatter and behind-camera prompters even when they read the same script.
- **Modern Arabic RTL Captions & Source QR Codes:** Bundles **Playpen Sans Arabic** (`assets/fonts/`) and renders 2–4 word highlighted RTL captions via Edge/Chrome HarfBuzz shaping (with Pillow fallback) + corner QR badges for citations.

---

## Quick Setup (Windows & Linux)

### 1. Windows (PowerShell)
```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
copy .env.example .env
```

### 2. Linux (Bash)
```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env
# Recommended on Linux for modern Arabic HarfBuzz captions:
# sudo apt-get install -y ffmpeg chromium fonts-noto-core
```
> FFmpeg is automatically provided via `imageio-ffmpeg` if `ffmpeg` is not on your `PATH`.

---

## `filmcraft-cli` Setup (Optional Recommended NLE Backend)

ReelCut automatically detects `filmcraft-cli` in this order:
1. `REELCUT_FILMCRAFT_PATH` in `.env`
2. **Portable binary inside the repo `bin/` folder** (ignored by Git via `.gitignore`):
   - **Windows:** `bin/filmcraft-cli.exe`
   - **Linux:** `bin/filmcraft-cli` (with `chmod +x bin/filmcraft-cli`)
3. `filmcraft-cli` on your system `PATH`
4. Standard OS install locations:
   - **Windows:** `C:\Program Files\ArtCraft\FilmCraft\filmcraft-cli.exe`
   - **Linux:** `/usr/local/bin/filmcraft-cli`, `/opt/artcraft/filmcraft-cli`, `/usr/bin/filmcraft-cli`

If `filmcraft-cli` is not present, ReelCut automatically uses the cross-platform `FFmpeg` fallback (`REELCUT_RENDER_BACKEND=ffmpeg` also forces FFmpeg).

---

## Video Color Filters (`color_preset`)
Set `REELCUT_COLOR_PRESET` in `.env` or pass `"color_preset"` inside `EditPlan`:
- `none` (default)
- `clean` (subtle contrast, vibrance, and clarity)
- `warm` (warmer skin-tone balance)
- `cinematic` (richer contrast + subtle vignette)
- `crisp` (extra sharpness and contrast)
- `bw` (high-contrast black & white)

---

## Connect as an MCP Server
Copy `mcp_config.example.json` into your client's `mcp_config.json` (e.g. `~/.gemini/config/mcp_config.json`) and point `command` to `.venv\Scripts\python.exe` (Windows) or `.venv/bin/python` (Linux).

## Verify Installation
```powershell
# Windows
.venv\Scripts\python.exe -m pytest tests -v

# Linux
.venv/bin/pytest tests -v
```

## Video Project Folder Layout
```text
my_video_project/
├── clips/                # Raw talking-head clips (or place directly in project root)
├── script.txt            # Optional written Arabic script (or script_resources.txt with URLs)
├── brolls/               # Optional project B-rolls
├── work/                 # Intermediate audio, transcripts, hybrid_speech.mp3, edit_plan.json
└── output/
    ├── final_reel.mp4    # Rendered 1080x1920 MP4
    └── project.fcproj    # Editable FilmCraft project (when FilmCraft backend is used)
```

## CLI Usage
```powershell
.venv\Scripts\reelcut.exe prepare-hybrid path\to\project
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



