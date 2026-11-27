"""Flow video B-roll provider — the browser path (real Omni 1.2 video).

Vendored from the reels-maker Flow driver (`flowkit`) with only the parts this
provider needs; nothing from that project's pipeline is included. One Chrome
profile, one job at a time, and the same hard-won rules:

1. The composer is agent-native ("Describe your goal"), so every prompt must
   ask for a VIDEO explicitly; the agent uses the project's Video defaults.
2. "Confirm before generating = Always" silently cancels the render, so it is
   set to Never before the first submission.
3. The CLI's rename step can time out **after** a render is paid for, so we
   poll the grid and download the new video tile ourselves.

Requires: the automation Chrome profile logged into Google Flow, the target
project uuid, and credits (~3 per second, 3 s minimum).
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

COST_PER_SEC = 3  # Omni 1.2: 3 credits/s up to 10 s
CDP = "http://127.0.0.1:9223"
DEFAULT_PROJECT = os.environ.get("REELCUT_FLOW_PROJECT", "")


# ------------------------------------------------------------------- readiness
def available() -> dict[str, Any]:
    """Is the browser path usable right now? Never spends anything."""
    try:
        with urllib.request.urlopen(CDP + "/json/version", timeout=3):
            cdp = True
    except Exception:  # noqa: BLE001
        cdp = False
    try:
        from .flowkit import chrome  # noqa: F401  (import check)

        importable = True
    except Exception:  # noqa: BLE001
        importable = False
    project = DEFAULT_PROJECT.strip()
    return {
        "configured": bool(cdp and importable and project),
        "chrome": cdp,
        "driver": importable,
        "project": project,
        "hint": ("Start the automation Chrome (logged into Flow), set "
                 "REELCUT_FLOW_PROJECT=<uuid>, then retry."
                 if not (cdp and importable and project) else ""),
    }


def estimate_credits(spec: list[dict[str, Any]]) -> int:
    return sum(max(3, int(round(float(c.get("duration", 3))))) * COST_PER_SEC
               for c in spec)


# --------------------------------------------------------------- page helpers
def _policy_never(page, log: Callable[[str], None]) -> None:
    page.locator('button[aria-label="Agent settings"]').first.click(timeout=8000)
    page.wait_for_timeout(2200)
    never = page.locator("text=Never").first
    if never.count():
        never.click(timeout=8000)
        page.wait_for_timeout(1200)
        log("[flow] Confirm before generating = Never")
    page.keyboard.press("Escape")
    page.wait_for_timeout(800)


def _prompt_box(page):
    loc = page.locator(
        'div.ProseMirror[aria-label="Describe your goal"], '
        'div.ProseMirror[aria-label="What do you want to create?"]')
    for i in range(loc.count()):
        try:
            if loc.nth(i).is_visible():
                return loc.nth(i)
        except Exception:  # noqa: BLE001
            pass
    return loc.first


def _composer(page, log: Callable[[str], None]) -> None:
    for _ in range(5):
        try:
            if _prompt_box(page).is_visible():
                return
        except Exception:  # noqa: BLE001
            pass
        close = page.locator('button[aria-label="Close"]')
        if close.count() and close.first.is_visible():
            close.first.click(timeout=6000)
            page.wait_for_timeout(1600)
            log("[flow] closed the session panel")
            continue
        page.wait_for_timeout(1200)
    raise RuntimeError("no prompt box on the project page")


def _submit(page, prompt: str) -> None:
    box = _prompt_box(page)
    try:
        box.click(timeout=6000)
    except Exception:  # noqa: BLE001
        box.evaluate("e => { e.focus(); }")
    page.wait_for_timeout(500)
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    page.keyboard.type(prompt, delay=6)
    page.wait_for_timeout(700)
    page.locator('button[aria-label="Start generation"]').first.click(
        timeout=10000)


def _wait_new_video(page, before: set[str], log: Callable[[str], None],
                    timeout_s: int = 900) -> str | None:
    from .flowkit import pages

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        page.wait_for_timeout(8000)
        try:
            tiles = pages.tiles(page)
        except Exception:  # noqa: BLE001
            continue
        fresh = [t for t in tiles if t["name"] not in before]
        vids = [t for t in fresh if t["kind"] == "video" and not t["busy"]]
        if vids:
            return vids[0]["name"]
        if fresh and not any(t["busy"] for t in fresh):
            log("[flow] the agent returned an image, not a video")
            return None
    return None


def _normalize(src: Path, dst: Path, seconds: float) -> Path:
    from .media import get_ffmpeg_exe

    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [get_ffmpeg_exe(), "-y", "-v", "error", "-i", str(src),
         "-vf", "scale=1080:1920:flags=lanczos,setsar=1,fps=30",
         "-t", f"{seconds}", "-c:v", "libx264", "-crf", "18",
         "-preset", "medium", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
         str(dst)], check=True, capture_output=True)
    return dst


# ------------------------------------------------------------------- generator
def generate(spec: list[dict[str, Any]], out_dir: str | Path,
             project: str | None = None, job_path: str | Path | None = None,
             log: Callable[[str], None] = print) -> dict[str, Any]:
    """Render each spec entry as a real video clip into out_dir."""
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    project = (project or DEFAULT_PROJECT).strip()
    if not project:
        raise RuntimeError("REELCUT_FLOW_PROJECT is not set (Flow project uuid)")
    tmp = out.parent / "_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    for key in ("TMP", "TEMP", "TMPDIR"):
        os.environ[key] = str(tmp)

    from .flowkit import assets, chrome, pages
    from playwright.sync_api import sync_playwright

    state: dict[str, Any] = {"status": "running", "provider": "flow_video",
                             "clips": [],
                             "started": time.strftime("%Y-%m-%dT%H:%M:%S")}

    def save() -> None:
        if job_path:
            Path(job_path).parent.mkdir(parents=True, exist_ok=True)
            Path(job_path).write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    save()
    with sync_playwright() as p:
        chrome.configure(profile=None, port=None, u_index=0)
        chrome.ensure_chrome()
        _browser, ctx = chrome.attach(p)
        page = ctx.new_page()
        pages.open_project(page, project)
        page.wait_for_timeout(5000)
        pages.dismiss_modals(page)
        _composer(page, log)
        _policy_never(page, log)

        for i, clip in enumerate(spec, 1):
            name = clip["name"]
            dest = out / name
            if dest.is_file() and dest.stat().st_size > 102400:
                state["clips"].append({"name": name, "status": "cached"})
                log(f"[flow {i}/{len(spec)}] {name} exists")
                save()
                continue
            seconds = max(3, int(round(float(clip.get("duration", 3)))))
            prompt = (f"Make a {seconds}-second vertical 9:16 VIDEO, one "
                      f"continuous shot: {clip['prompt']}")
            before = {t["name"] for t in pages.tiles(page)}
            try:
                _submit(page, prompt)
                log(f"[flow {i}/{len(spec)}] {name} submitted ({seconds}s)")
                tile = _wait_new_video(page, before, log)
                if not tile:
                    state["clips"].append({"name": name,
                                           "status": "no_video_tile"})
                    save()
                    continue
                raw = out / "raw" / name
                settled = assets.ensure_ready(page, tile, timeout_s=600,
                                              log=log)
                assets.download_tile(page, settled, raw, kind="video", log=log)
                _normalize(raw, dest, seconds)
                state["clips"].append({"name": name, "status": "done",
                                       "bytes": dest.stat().st_size})
                log(f"[flow {i}] {name} done")
            except Exception as exc:  # noqa: BLE001 - one clip must not kill all
                state["clips"].append({"name": name, "status": "failed",
                                       "error": f"{type(exc).__name__}: {exc}"[:300]})
                log(f"[flow {i}] {name} failed: {exc}")
            save()

    ok = sum(1 for c in state["clips"] if c["status"] in ("done", "cached"))
    state.update(status="done" if ok == len(spec) else "partial",
                 finished=ok, total=len(spec),
                 finished_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
    save()
    return state
