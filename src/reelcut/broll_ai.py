"""B-roll generation from an EditPlan, provider-based.

Providers
---------
gemini_image (default, no browser)
    Each `broll_suggestions[i].prompt` becomes a 9:16 still via the Gemini image
    model, then a short Ken Burns move turns it into a silent mp4 clip with the
    suggested filename. Cheap (cents), no Chrome, no ToS risk.
flow_video
    Reserved for the browser Flow path (Omni video); returns not_configured
    unless that tool is wired in, so callers can degrade gracefully.
stock
    Falls back to Pexels/Pixabay when API keys exist (existing brolls helper).
manual
    No generation: tells the creator which filenames to drop in `brolls/`.

Every provider returns the same contract: status, clips, estimate, attachments
(segment_index -> broll_id) once the folder is indexed, and a job file so long
batches can be watched from an MCP client.
"""
from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from typing import Any

API = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_IMAGE_MODEL = os.environ.get("GEMINI_IMAGE_MODEL",
                                     "gemini-2.5-flash-image")
IMAGE_USD = {"gemini-2.5-flash-image": 0.039,
             "gemini-3.1-flash-image": 0.05,
             "gemini-3-pro-image": 0.13}
PROVIDERS = ("gemini_image", "flow_video", "stock", "manual")
MIN_SEC, MAX_SEC = 2.0, 8.0
STYLE_SUFFIX = (" Vertical 9:16 composition, photorealistic unless the prompt "
                "says otherwise, no text, no captions, no logos, no watermark.")


# ------------------------------------------------------------------ plan input
def _plan(project_dir: str | Path) -> dict[str, Any]:
    path = Path(project_dir) / "work" / "edit_plan.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def plan_prompts(project_dir: str | Path) -> list[dict[str, Any]]:
    """Suggestions -> generation specs (exact filename, clamped duration)."""
    plan = _plan(project_dir)
    specs: list[dict[str, Any]] = []
    for i, sug in enumerate(plan.get("broll_suggestions") or [], 1):
        prompt = " ".join(str(sug.get("prompt") or "").split())
        if not prompt:
            continue
        name = str(sug.get("suggested_filename") or f"{i:02d}_broll.mp4")
        if not name.lower().endswith(".mp4"):
            name += ".mp4"
        dur = max(MIN_SEC, min(MAX_SEC, float(sug.get("duration_sec") or 3.0)))
        specs.append({"name": name, "duration": round(dur, 2),
                      "prompt": prompt,
                      "segment_index": int(sug.get("segment_index") or i)})
    return specs


def estimate(specs: list[dict[str, Any]], provider: str = "gemini_image",
             model: str = DEFAULT_IMAGE_MODEL) -> dict[str, Any]:
    if provider == "flow_video":
        from .broll_flow import estimate_credits

        return {"provider": provider, "clips": len(specs), "images": 0,
                "usd": 0.0, "credits": estimate_credits(specs),
                "seconds": sum(max(3, round(float(c.get("duration", 3))))
                               for c in specs)}
    if provider != "gemini_image":
        return {"provider": provider, "clips": len(specs), "images": 0,
                "usd": 0.0, "credits": 0}
    usd = round(IMAGE_USD.get(model, 0.05) * len(specs), 3)
    return {"provider": provider, "clips": len(specs), "images": len(specs),
            "usd": usd, "credits": 0, "model": model}


# ------------------------------------------------------------------- generation
def _key() -> str:
    from .transcribe_gemini import load_key

    return load_key()


def generate_image(prompt: str, model: str = DEFAULT_IMAGE_MODEL,
                   key: str | None = None, retries: int = 2) -> bytes:
    """One 9:16 still as PNG bytes (inline image part)."""
    import httpx

    key = key or _key()
    body = {
        "contents": [{"role": "user", "parts": [
            {"text": prompt + STYLE_SUFFIX}]}],
        "generationConfig": {"response_modalities": ["IMAGE"],
                             "image_config": {"aspect_ratio": "9:16"}},
    }
    last = ""
    for attempt in range(retries + 1):
        with httpx.Client(timeout=180) as client:
            r = client.post(f"{API}/models/{model}:generateContent",
                            params={"key": key}, json=body)
        if r.status_code < 400:
            parts = (r.json().get("candidates") or [{}])[0].get("content", {}) \
                .get("parts") or []
            for part in parts:
                blob = part.get("inlineData") or part.get("inline_data") or {}
                if blob.get("data"):
                    return base64.b64decode(blob["data"])
            last = "no image part in the response"
        else:
            last = f"HTTP {r.status_code}: {r.text[:200]}"
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"image generation failed ({model}): {last}")


def ken_burns(image: Path, out: Path, duration: float, push_in: bool = True,
              size: tuple[int, int] = (1080, 1920)) -> Path:
    """Silent mp4: slow zoom over the still (ffmpeg only, no extra deps)."""
    from .media import get_ffmpeg_exe

    w, h = size
    frames = max(1, int(round(duration * 30)))
    if push_in:
        z = "min(zoom+0.0009,1.14)"
    else:
        z = "if(lte(zoom,1.0),1.14,max(1.001,zoom-0.0009))"
    vf = (f"scale={int(w*1.15)}:-2,"
          f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
          f"d={frames}:s={w}x{h}:fps=30,setsar=1")
    out.parent.mkdir(parents=True, exist_ok=True)
    import subprocess

    # d=frames + an explicit frame cap gives an exact duration (a bare -t on a
    # looped still drifts: 2 s came out as 1.67 s)
    subprocess.run([get_ffmpeg_exe(), "-y", "-v", "error", "-loop", "1",
                    "-i", str(image), "-vf", vf, "-frames:v", str(frames),
                    "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                    str(out)], check=True, capture_output=True)
    return out


def _index_and_attach(project: Path, specs: list[dict[str, Any]]
                      ) -> list[dict[str, Any]]:
    from .brolls import index_local_brolls

    brolls = project / "brolls"
    items = index_local_brolls(brolls)
    by_name = {Path(i.get("path", "")).name: i for i in items}
    out = []
    for s in specs:
        item = by_name.get(s["name"])
        if item:
            out.append({"segment_index": s["segment_index"],
                        "broll_id": item["broll_id"],
                        "path": item["path"],
                        "duration": item.get("duration")})
    return out


def _job_path(project: Path) -> Path:
    return project / "work" / "broll_job.json"


def _job_write(project: Path, state: dict[str, Any]) -> None:
    path = _job_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def generate_brolls(project_dir: str | Path, provider: str = "gemini_image",
                    confirm: bool = False, max_clips: int | None = None,
                    budget_usd: float | None = None,
                    budget_credits: int | None = None,
                    model: str = DEFAULT_IMAGE_MODEL,
                    log=print) -> dict[str, Any]:
    """Generate the plan's B-rolls. Nothing is spent without confirm=True."""
    project = Path(project_dir).resolve()
    specs = plan_prompts(project)
    if max_clips:
        specs = specs[:max_clips]
    est = estimate(specs, provider, model)
    base = {"project_dir": str(project), "provider": provider,
            "clips": len(specs), "estimate": est}

    if provider not in PROVIDERS:
        return {**base, "status": "bad_provider",
                "hint": f"choose one of {PROVIDERS}"}
    if provider == "stock":
        return {**base, "status": "not_configured",
                "hint": ("provider 'stock' needs PEXELS_API_KEY/PIXABAY_API_KEY; "
                         "use provider='gemini_image' or 'flow_video'")}
    if provider == "manual":
        return {**base, "status": "manual_only",
                "hint": "Add these files to brolls/, then call index_brolls:",
                "files": [s["name"] for s in specs]}
    if not specs:
        return {**base, "status": "nothing_to_generate",
                "hint": "The plan has no broll_suggestions; run save_edit_plan "
                        "with suggestions first."}

    if provider == "flow_video":
        from .broll_flow import available, generate as flow_generate

        info = available()
        if not info["configured"]:
            return {**base, "status": "not_configured",
                    "hint": info["hint"], "flow": info}
        if not confirm:
            return {**base, "status": "needs_confirmation",
                    "hint": (f"Re-call with confirm=True to spend about "
                             f"{est['credits']} credits on "
                             f"{est['clips']} Flow video clips.")}
        if budget_credits is not None and est["credits"] > budget_credits:
            return {**base, "status": "budget_exceeded",
                    "hint": f"estimate {est['credits']} credits > "
                            f"budget {budget_credits}"}
        brolls = project / "brolls"
        brolls.mkdir(parents=True, exist_ok=True)
        state = flow_generate(specs, brolls, project=info["project"],
                              job_path=_job_path(project), log=log)
        attachments = _index_and_attach(project, specs)
        return {**base, "status": state["status"],
                "finished": state.get("finished", 0),
                "attachments": attachments,
                "hint": "Call save_edit_plan with these broll_ids."}

    if not confirm:
        return {**base, "status": "needs_confirmation",
                "hint": (f"Re-call with confirm=True to spend about "
                         f"${est['usd']:.2f} on {est['images']} images.")}
    if budget_usd is not None and est["usd"] > budget_usd:
        return {**base, "status": "budget_exceeded",
                "hint": f"estimate ${est['usd']} > budget ${budget_usd}"}

    brolls = project / "brolls"
    brolls.mkdir(parents=True, exist_ok=True)
    state: dict[str, Any] = {"status": "running", "started": time.strftime(
        "%Y-%m-%dT%H:%M:%S"), "provider": provider, "clips": []}
    _job_write(project, state)
    key = _key()
    for i, spec in enumerate(specs, 1):
        clip_path = brolls / spec["name"]
        if clip_path.is_file() and clip_path.stat().st_size > 20000:
            state["clips"].append({"name": spec["name"], "status": "cached"})
            log(f"[{i}/{len(specs)}] {spec['name']} exists")
        else:
            try:
                png = generate_image(spec["prompt"], model=model, key=key)
                img = brolls / f".{spec['name']}.png"
                img.write_bytes(png)
                ken_burns(img, clip_path, spec["duration"],
                          push_in=(i % 2 == 1))
                img.unlink(missing_ok=True)
                state["clips"].append({"name": spec["name"], "status": "done",
                                       "bytes": clip_path.stat().st_size})
                log(f"[{i}/{len(specs)}] {spec['name']} "
                    f"{spec['duration']}s")
            except Exception as exc:  # noqa: BLE001 - isolate one bad prompt
                state["clips"].append({"name": spec["name"], "status": "failed",
                                       "error": f"{type(exc).__name__}: {exc}"[:300]})
                log(f"[{i}/{len(specs)}] FAILED {spec['name']}: {exc}")
        _job_write(project, state)

    ok = sum(1 for c in state["clips"] if c["status"] in ("done", "cached"))
    attachments = _index_and_attach(project, specs)
    state.update(status="done" if ok == len(specs) else "partial",
                 finished=ok, total=len(specs),
                 finished_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                 attachments=attachments)
    _job_write(project, state)
    return {**base, "status": state["status"], "finished": ok,
            "attachments": attachments,
            "hint": "Call save_edit_plan with these broll_ids."}


def status(project_dir: str | Path) -> dict[str, Any]:
    project = Path(project_dir)
    path = _job_path(project)
    state = None
    if path.is_file():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            state = {"status": "unreadable"}
    brolls = project / "brolls"
    return {"job": state,
            "clips_on_disk": sorted(p.name for p in brolls.glob("*.mp4"))
            if brolls.is_dir() else [],
            "provider": (state or {}).get("provider")}
