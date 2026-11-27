# AI tools: voiceover transcription + B-roll generation (Gemini)

Two creator-facing capabilities, both exposed as MCP tools and CLI commands.
The editor stays local and free; only these two steps call a paid API, and both
quote before spending.

## 1. `transcribe_voiceover` — clean script + cut-safe seconds

Raw voiceover sessions contain retakes, stumbles and side chatter. This runs two
passes on purpose:

| pass | who | what you get |
|---|---|---|
| A — text | Gemini 3.5 Flash + the voiceover-editor system instruction | the final script: best take per line, stumbles dropped, chatter excluded, pickups stitched, dialect preserved |
| B — time | Silero VAD (local, free) | real speech windows; the script lines are aligned onto them |

Why not take Gemini's timestamps directly: measured on a 121 s session, one
"segment" spanned 44 s while only ~10 s of speech existed, and later segments
pointed past the end of the audio. Seconds must come from the audio.

Each returned segment therefore starts and ends **in silence** — a cut list you
can render without touching it afterwards. Unmatched windows are reported in
`dropped[]` instead of being silently included.

```powershell
reelcut transcribe-gemini path\to\project --model gemini-3.5-flash
```

Output: `work/gemini_transcript.json` (script, pickups, segments, dropped,
speech_seconds, windows) plus a printed timeline.

Tuning: `MIN_SIMILARITY` (default 0.45) in `transcribe_gemini.py` decides how
much token overlap a line needs to claim a window; `MAX_SPAN` (3) lets one
sentence span short pauses.

## 2. `generate_brolls` — 9:16 clips from the plan's suggestions

Providers:

| provider | what it does | cost |
|---|---|---|
| `gemini_image` (default) | Gemini image model makes a 9:16 still per suggestion, ffmpeg adds a Ken Burns move → silent mp4 with the suggested filename | ≈ $0.04/image (`gemini-2.5-flash-image`) |
| `manual` | prints the exact filenames to drop into `brolls/` | free |
| `stock` | Pexels/Pixabay (needs keys) | free |
| `flow_video` | real **Omni 1.2 video** through the automation Chrome (vendored `flowkit`); downloads by polling the grid so a paid render is never lost | ≈ 3 credits/second (3 s min) |

```powershell
reelcut brolls path\to\project                 # quote only
reelcut brolls path\to\project --confirm --budget-usd 1.00
```

Contract: refuses without `confirm=True`, refuses over `budget_usd`, skips clips
that already exist, writes `work/broll_job.json` (progress + per-clip status) and
returns `attachments` (`segment_index` → `broll_id`) for `save_edit_plan`.

Measured: 1 still in **7.7 s** (768×1344) → **3.00 s** 1080×1920 clip at 0.72 MB.

### Flow video provider (`flow_video`)

Prerequisites: the automation Chrome profile logged into Google Flow, and the
target project uuid in `REELCUT_FLOW_PROJECT`. Readiness is checkable without
spending: `python -c "from reelcut import broll_flow; print(broll_flow.available())"`.

```powershell
reelcut brolls path\to\project --provider flow_video            # quote in credits
reelcut brolls path\to\project --provider flow_video --confirm --budget-credits 60
```

Same guarantees as the image provider (quote first, `confirm=True`, hard budget)
plus the rules the browser path needs: the project credit policy is set to
Never (an unanswered dialog silently cancels), prompts explicitly ask for a
VIDEO, and each finished tile is downloaded by name before the next submission.


## Keys and limits

- `GEMINI_API_KEY` in `.env` (git-ignored). Never commit it.
- Image output is ~1K (768×1344 for 9:16), upscaled to 1080×1920 by the camera
  move — fine for B-roll, not for a hero shot.
- The image model can render text/logos; prompts carry "no text, no captions,
  no logos, no watermark" and stills with lettering should be regenerated.
- Both tools degrade honestly: no key → a clear error; no suggestions → nothing
  to generate; unsupported provider → `not_configured`.


## First run: when is a Google login actually needed?

| path | login | what the user provides |
|---|---|---|
| `transcribe_voiceover` | **never** | `GEMINI_API_KEY` in `.env` |
| `generate_brolls(provider="gemini_image")` | **never** | same key |
| `generate_brolls(provider="flow_video")` | **once, ever** | Chrome signed into Flow + a project uuid |

Only the browser path needs a session, and only its first use. Two ways to set
it up (both spend nothing):

```powershell
reelcut flow-login --wait 180                 # opens Chrome, waits for sign-in
reelcut flow-login --project <uuid>           # saves REELCUT_FLOW_PROJECT to .env
```

It verifies the account, lists the visible projects (`title -> uuid`) and writes
`~/.reelcut/flow_session.json`. From an MCP-only client, call `flow_login`
first: with nobody signed in it returns `status="sign_in_required"` and a hint —
the creator signs in once in the window that opened, then the call is repeated.

Failure ordering is deliberate: `broll_flow.available()` checks Chrome, the
driver and the project uuid **before** any spend, and `generate_brolls` returns
`not_configured` with the exact missing piece instead of failing mid-render.

## 3. `review_takes` — visual check (audio alone is not enough)

Alignment proves the words match. It cannot prove the take is *presentation*:
in real sessions the creator checks the phone, glances at notes, turns away or
talks to somebody off camera **while still saying the line**. A cut built from
audio only keeps those "preparation" moments — which is exactly why an
agent-driven edit that looked at frames beat an audio-only cut on the same
recording.

```powershell
reelcut review-takes path\to\project            # verdict per planned take
reelcut review-takes path\to\project --apply    # also apply the suggested trims
```

Per take it samples frames (default 3) and asks a vision model one question —
`person|eye_contact|usable|reason`, where person is camera / phone / notes /
away / other_person / none. The take is usable only when the face is toward the
lens and the hands are free; `trim_start`/`trim_end` remove preparation frames
without touching the spoken words.

- Cost: image tokens only (~260 per frame, so a 9-take plan is a few cents).
- Unusable takes are reported (`preparation_takes`) — replace them with another
  take of the same line from the transcript, then render.
- A failed frame never counts as a pass (a blind sample fails closed).

Workflow: `transcribe_voiceover` → choose takes → `save_edit_plan` →
**`review_takes`** → replace/trim → `render_video`.