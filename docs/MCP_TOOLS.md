# ReelCut MCP Tools & Schema Reference

Server entry point: `python -m reelcut.mcp_server` (stdio transport via `mcp.server.mcpserver.MCPServer`).

---

## 1. `prepare_project`
Runs local CPU-only media processing (0 LLM tokens) and writes `work/transcript_compact.json` and `work/words_detail.json`.

**Parameters:**
- `project_dir` (`str`, required): Folder containing raw clips (in `clips/` or root) and optional `script.txt` / `script_resources.txt`.
- `whisper_model` (`str`, default `"small"`): Faster-Whisper CPU model (`tiny`, `base`, `small`, `medium`, `large-v3-turbo`).
- `auto_trim_silence` (`bool`, default `true`): Enables Silero VAD pause detection & turn splitting.
- `brolls_dir` (`str | null`, default `null`): Optional shared local B-roll library directory.

**Returns:**
Compact dictionary containing `script_text`, `clips`, `segments` (`id`, `clip_id`, `start`, `end`, `text`), and `local_brolls` (`broll_id`, `tags`, `duration`).

---

## 2. `index_brolls`
Indexes a local folder of videos/images once into `brolls_index.json` using filename/folder tags and media dimensions/duration.

**Parameters:**
- `brolls_dir` (`str`, required)

---

## 3. `fetch_brolls`
Downloads portrait (`9:16`) stock videos from Pexels/Pixabay into `<project_dir>/work/brolls_cache/` and indexes them.
If `PEXELS_API_KEY` and `PIXABAY_API_KEY` are unset, returns `status: "api_not_configured"` gracefully without failing.

**Parameters:**
- `queries` (`list[str]`, required): Short English search keywords.
- `project_dir` (`str`, required)

---

## 4. `save_edit_plan`
Validates an `EditPlan` dictionary, aligns corrected colloquial Arabic words onto timeline timestamps, resolves B-rolls & QR badges, writes `work/edit_plan.json` and `work/timeline_resolved.json`, and returns a 2-line Arabic approval summary (`summary_2_lines`).

**`plan` Schema (`EditPlan`):**
```json
{
  "title": "Optional title",
  "summary_ar": "Optional notes",
  "segments": [
    {
      "clip_id": "c01",
      "start": 12.4,
      "end": 16.8,
      "corrected_text": "النص المصحح بالعامية",
      "zoom": true,
      "broll": {
        "broll_id": "b001",
        "offset_sec": 0.2,
        "duration_sec": 2.0
      },
      "qr_url": "https://example.com/study",
      "qr_label": "المصدر"
    }
  ]
}
```
- `broll`, `zoom`, `qr_url` are optional.
- `broll` accepts `broll_id`, explicit `path`, or `query`.

---

## 5. `render_video`
Executes local FFmpeg + Pillow RTL caption rendering and outputs `<project_dir>/output/final_reel.mp4` (`1080x1920`, H.264/AAC, `-14 LUFS`).

**Parameters:**
- `project_dir` (`str`, required)

**Returns:**
`{"status": "rendered", "output_path": "...", "duration": ..., "resolution": "1080x1920", "size_mb": ...}`
