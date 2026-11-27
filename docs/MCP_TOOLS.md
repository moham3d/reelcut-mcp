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


---

## 6. `transcribe_voiceover`
Gemini clean-script pass (voiceover editor system instruction: final take only,
stumbles/chatter dropped, pickups stitched, dialect preserved) + a local Silero
VAD pass that assigns real seconds. Writes `work/gemini_transcript.json`.
**Params:** `project_dir`, `audio` (optional path), `model` (default `gemini-3.5-flash`).
**Returns:** `segments` (`start`,`end`,`text`,`score`), `dropped`, `script`,
`speech_seconds`, `windows`, `transcript_path`. Requires `GEMINI_API_KEY`.

## 7. `quote_brolls`
Free quote for the plan's `broll_suggestions`: filenames, durations and the
estimated USD for the chosen provider.

## 8. `generate_brolls`
Renders the plan's B-rolls into `brolls/` with the suggested filenames.
**Params:** `project_dir`, `provider` (`gemini_image`|`flow_video`|`manual`|`stock`),
`confirm` (nothing is spent without it), `max_clips`, `budget_usd` (images),
`budget_credits` (Flow video, ~3 credits/second).
**Returns:** `status` (`needs_confirmation`|`budget_exceeded`|`done`|`partial`|
`manual_only`|`not_configured`), `estimate`, `attachments`
(`segment_index` → `broll_id`), `hint`.

## 9. `broll_status`
Progress of the last batch (`work/broll_job.json`) + clips already on disk.



---

## 10. `flow_login`
One-time Flow setup for the `flow_video` provider (no credits spent). Opens the
automation Chrome on flow.google.com; returns `status="sign_in_required"` when
nobody is signed in (the creator signs in once, then the call is repeated), and
on success the account, visible projects (`title`/`uuid`) and, when given,
the project uuid saved to `.env` as `REELCUT_FLOW_PROJECT`.
**Params:** `project` (optional uuid), `wait_seconds` (0 = single check).


---

## 11. `review_takes`
Visual check of the planned takes: presenting to camera, or preparing (phone,
notes, turned away, talking to somebody else)? Samples frames per segment and
returns `usable`, `ratio`, `reason`, `trim_start`/`trim_end`, plus
`preparation_takes` (indices that must be replaced). Writes
`work/vision_review.json`. **Params:** `project_dir`, `samples` (3).
Requires `GEMINI_API_KEY`; costs image tokens only.