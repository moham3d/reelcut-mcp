---
name: reelcut
description: >-
  Token-efficient local 9:16 Arabic video editing workflow using the ReelCut MCP
  server. Use when the user asks to edit, clean, caption, add B-rolls to, or
  produce a social media reel from a folder of raw talking-head clips and script.
---

# ReelCut Workflow

Use the `reelcut` MCP tools to edit segmented Arabic talking-head clips into a
9:16 social media reel while minimizing token consumption.

## Rules
1. **Never inspect raw video/audio files directly** — let `prepare_project` handle all media locally.
2. **Always show the 2-line Arabic plan and wait for explicit user approval** before calling `render_video`.
3. Keep tool payloads minimal: only build `EditPlan` from `transcript_compact.json`, `script_text`, and `local_brolls`.

## Steps

1. **Prepare locally (`prepare_project`):**
   - Call `prepare_project(project_dir=...)`.
   - Read the compact response: `script_text`, `segments` (`id`, `clip_id`, `start`, `end`, `text`), and `local_brolls`.

2. **Plan the edit as Director:**
   - Remove failed retakes/stumbles and keep the cleanest takes in script order.
   - **Ignore background/bystander voices:** drop any segment whose speech does not correspond to `script_text` (behind-camera prompters and bystanders are also filtered acoustically by `speaker.py`).
   - **Source URLs in script:** never speak or caption raw URLs from `script_resources.txt`; either exclude them or set `qr_url` on the matching segment to render a small corner QR code.
   - Fix colloquial Arabic spelling in `corrected_text` using `script_text`.
   - Set `zoom: true` occasionally (about 20-35% of cuts) for subtle 1.12x punch-in variety.
   - Attach B-rolls from `local_brolls` (`broll_id`) where visuals reinforce the spoken point; if stock B-rolls are needed and API keys are configured, call `fetch_brolls`.

3. **Save plan & get user approval (`save_edit_plan`):**
   - Call `save_edit_plan(project_dir=..., plan=...)`.
   - Present the returned `summary_2_lines` directly to the user in Arabic and stop to wait for confirmation.

4. **Render (`render_video`):**
   - Once the user approves, call `render_video(project_dir=...)` and share the resulting `output/final_reel.mp4` path, duration, and file size.
