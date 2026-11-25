"""Local B-roll indexing and optional stock B-roll fetching (Pexels / Pixabay)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx

from .config import ReelcutConfig
from .media import get_ffmpeg_exe, probe_media
from .models import BRollItem

BROLL_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".jpg", ".jpeg", ".png", ".webp"}


def _derive_tags(path: Path, root: Path) -> list[str]:
    rel = path.relative_to(root)
    tokens: list[str] = []
    for part in (*rel.parent.parts, rel.stem):
        for chunk in re.split(r"[\s_\-]+", part):
            clean = chunk.strip().lower()
            if clean and clean not in tokens:
                tokens.append(clean)
    return tokens


def index_local_brolls(
    brolls_dir: str | Path,
    index_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Index local B-roll videos/images once and save metadata + tags to JSON."""
    root = Path(brolls_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"B-rolls directory not found: {root}")

    out_index = Path(index_path).resolve() if index_path else (root / "brolls_index.json")
    existing_by_path: dict[str, dict[str, Any]] = {}
    if out_index.is_file():
        try:
            prev = json.loads(out_index.read_text(encoding="utf-8"))
            for item in prev:
                existing_by_path[str(Path(item["path"]).resolve())] = item
        except Exception:
            existing_by_path = {}

    cfg = ReelcutConfig.load()
    ffmpeg_exe = get_ffmpeg_exe(cfg.ffmpeg_path)

    files = sorted(
        (p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in BROLL_EXTENSIONS),
        key=lambda p: p.name.lower(),
    )

    items: list[dict[str, Any]] = []
    for idx, file_path in enumerate(files, start=1):
        resolved = str(file_path.resolve())
        prev = existing_by_path.get(resolved)
        if prev and prev.get("width") and prev.get("tags"):
            item = dict(prev)
            item["broll_id"] = prev.get("broll_id") or f"b{idx:03d}"
            items.append(item)
            continue

        tags = _derive_tags(file_path, root)
        if file_path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
            info_dur, info_w, info_h = 4.0, 1080, 1920
        else:
            info = probe_media(file_path, ffmpeg_exe=ffmpeg_exe)
            info_dur, info_w, info_h = round(info.duration, 2), info.width, info.height

        item = BRollItem(
            broll_id=f"b{idx:03d}",
            path=resolved,
            tags=tags,
            duration=info_dur,
            width=info_w,
            height=info_h,
            source="local",
        ).model_dump()
        items.append(item)

    out_index.parent.mkdir(parents=True, exist_ok=True)
    out_index.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    return items


def load_broll_index(
    project_dir: Path,
    shared_brolls_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Load available B-rolls from project `brolls/` and optional shared library."""
    combined: list[dict[str, Any]] = []
    seen_paths: set[str] = set()

    candidates: list[Path] = []
    proj_brolls = project_dir / "brolls"
    if proj_brolls.is_dir():
        candidates.append(proj_brolls)
    cache_brolls = project_dir / "work" / "brolls_cache"
    if cache_brolls.is_dir():
        candidates.append(cache_brolls)
    if shared_brolls_dir and shared_brolls_dir.is_dir():
        candidates.append(shared_brolls_dir)

    for folder in candidates:
        idx_file = folder / "brolls_index.json"
        if idx_file.is_file():
            try:
                items = json.loads(idx_file.read_text(encoding="utf-8"))
            except Exception:
                items = index_local_brolls(folder, idx_file)
        else:
            items = index_local_brolls(folder, idx_file)

        for item in items:
            p = item.get("path", "")
            if p and p not in seen_paths and Path(p).is_file():
                seen_paths.add(p)
                combined.append(item)

    return combined


def _download_file(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream("GET", url, follow_redirects=True, timeout=60.0) as resp:
        resp.raise_for_status()
        with dest.open("wb") as fh:
            for chunk in resp.iter_bytes(chunk_size=65536):
                fh.write(chunk)
    return dest


def fetch_stock_brolls(
    queries: list[str],
    cache_dir: str | Path,
    project_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Fetch vertical stock B-rolls from Pexels/Pixabay into local cache.

    Gracefully returns a notice when API keys are not configured yet.
    """
    cfg = ReelcutConfig.load(project_dir)
    if not cfg.pexels_api_key and not cfg.pixabay_api_key:
        return {
            "status": "api_not_configured",
            "message": (
                "No PEXELS_API_KEY or PIXABAY_API_KEY configured yet. "
                "Add a key to .env later to enable automatic stock B-roll downloads; "
                "local B-rolls continue to work with zero API keys."
            ),
            "downloaded": [],
        }

    dest_dir = Path(cache_dir).resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)
    downloaded: list[dict[str, Any]] = []

    for query in queries:
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", query.strip().lower()).strip("_") or "broll"
        target = dest_dir / f"{slug}.mp4"
        if target.is_file():
            downloaded.append({"query": query, "path": str(target), "cached": True})
            continue

        video_url: str | None = None
        source_name = ""

        if cfg.pexels_api_key:
            resp = httpx.get(
                "https://api.pexels.com/videos/search",
                params={"query": query, "orientation": "portrait", "per_page": 3},
                headers={"Authorization": cfg.pexels_api_key},
                timeout=30.0,
            )
            if resp.status_code == 200:
                videos = resp.json().get("videos", [])
                if videos:
                    files = videos[0].get("video_files", [])
                    hd = sorted(
                        (f for f in files if f.get("file_type") == "video/mp4"),
                        key=lambda f: abs((f.get("height") or 1920) - 1920),
                    )
                    if hd:
                        video_url = hd[0]["link"]
                        source_name = "pexels"

        if not video_url and cfg.pixabay_api_key:
            resp = httpx.get(
                "https://pixabay.com/api/videos/",
                params={"key": cfg.pixabay_api_key, "q": query, "per_page": 3},
                timeout=30.0,
            )
            if resp.status_code == 200:
                hits = resp.json().get("hits", [])
                if hits:
                    vids = hits[0].get("videos", {})
                    best = vids.get("medium") or vids.get("large") or vids.get("small")
                    if best and best.get("url"):
                        video_url = best["url"]
                        source_name = "pixabay"

        if video_url:
            _download_file(video_url, target)
            downloaded.append(
                {
                    "query": query,
                    "path": str(target),
                    "source": source_name,
                    "cached": False,
                }
            )

    index_local_brolls(dest_dir, dest_dir / "brolls_index.json")
    return {"status": "ok", "downloaded": downloaded}
