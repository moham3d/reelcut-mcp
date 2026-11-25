"""FilmCraft headless NLE rendering backend (frame-accurate A/V sync + editable .fcproj)."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image

from .captions import render_caption_states, render_qr_badge
from .config import ReelcutConfig
from .media import get_ffmpeg_exe, probe_media

TICKS_PER_SEC = 254_016_000_000


def _run_fc(fc_exe: str, args: list[str], stdin_text: str | None = None) -> str:
    proc = subprocess.run(
        [fc_exe, *args],
        input=stdin_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"filmcraft-cli failed (code {proc.returncode}):\n"
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc.stdout


def _run_fc_jsonl(fc_exe: str, proj_file: Path, commands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload = "\n".join(json.dumps(c, ensure_ascii=False) for c in commands) + "\n"
    out = _run_fc(fc_exe, ["--project", str(proj_file), "--save", "run", "-"], stdin_text=payload)
    results: list[dict[str, Any]] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        parsed = json.loads(line)
        if not parsed.get("ok", True):
            raise RuntimeError(f"FilmCraft command failed: {parsed}")
        results.append(parsed)
    return results


def _import_in_batches(fc_exe: str, proj_file: Path, paths: list[Path], batch_size: int = 40) -> dict[str, int]:
    """Import media files into FilmCraft and return {resolved_path: item_id}."""
    mapping: dict[str, int] = {}
    unique_paths: list[Path] = []
    seen: set[str] = set()
    for p in paths:
        rp = str(p.resolve())
        if rp not in seen:
            seen.add(rp)
            unique_paths.append(p.resolve())

    for i in range(0, len(unique_paths), batch_size):
        batch = unique_paths[i : i + batch_size]
        out = _run_fc(
            fc_exe,
            ["--project", str(proj_file), "--save", "import", *(str(p) for p in batch)],
        )
        data = json.loads(out)
        items = data.get("items", [])
        for p, item_id in zip(batch, items):
            mapping[str(p)] = int(item_id)
    return mapping


def _extract_clip_anchors(fc_exe: str, proj_file: Path) -> dict[int, tuple[float, float]]:
    """Read actual decoded (width, height) per placed video clip via motion anchor."""
    raw = _run_fc(fc_exe, ["--project", str(proj_file), "inspect", "sequence"])
    seq = json.loads(raw)
    dims: dict[int, tuple[float, float]] = {}
    for track in seq.get("video", []):
        for item in track.get("items", []):
            cid = int(item["clip"])
            for eff in item.get("effects", []):
                if eff.get("effect") == "motion":
                    anchor_val = str(eff.get("params", {}).get("anchor", {}).get("value", ""))
                    m = re.search(r"x:\s*([\d.]+),\s*y:\s*([\d.]+)", anchor_val)
                    if m:
                        dims[cid] = (float(m.group(1)) * 2.0, float(m.group(2)) * 2.0)
    return dims


def _remap_words_to_frames(
    segments: list[dict[str, Any]],
    timeline_words: list[dict[str, Any]],
    fps: int,
) -> tuple[list[dict[str, Any]], list[tuple[int, int]]]:
    """Quantize segment durations to exact frames and align words to frame boundaries."""
    frame_ranges: list[tuple[int, int]] = []
    cursor_frames = 0
    remapped_words: list[dict[str, Any]] = []

    for seg in segments:
        old_start = float(seg["timeline_start"])
        old_end = float(seg["timeline_end"])
        old_dur = max(0.05, old_end - old_start)

        seg_frames = max(1, int(round(old_dur * fps)))
        new_start = cursor_frames / fps
        new_end = (cursor_frames + seg_frames) / fps
        new_dur = new_end - new_start

        for w in timeline_words:
            mid = (float(w["start"]) + float(w["end"])) / 2.0
            if old_start - 0.02 <= mid <= old_end + 0.02:
                rel_s = max(0.0, (float(w["start"]) - old_start) / old_dur)
                rel_e = min(1.0, max(rel_s + 0.02, (float(w["end"]) - old_start) / old_dur))
                remapped_words.append(
                    {
                        "word": w["word"],
                        "start": round(new_start + rel_s * new_dur, 4),
                        "end": round(new_start + rel_e * new_dur, 4),
                    }
                )

        frame_ranges.append((cursor_frames, seg_frames))
        cursor_frames += seg_frames

    return remapped_words, frame_ranges


def render_with_filmcraft(
    project_dir: Path,
    timeline: dict[str, Any],
    cfg: ReelcutConfig,
) -> dict[str, Any]:
    """Render 9:16 MP4 and save editable .fcproj using FilmCraft headless CLI."""
    fc_exe = cfg.filmcraft_path
    if not fc_exe or not Path(fc_exe).is_file():
        raise FileNotFoundError("FilmCraft CLI executable not found.")

    work_dir = project_dir / "work"
    output_dir = project_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    fcproj = output_dir / "project.fcproj"
    final_mp4 = output_dir / "final_reel.mp4"
    fcproj.unlink(missing_ok=True)
    final_mp4.unlink(missing_ok=True)

    segments = timeline.get("segments", [])
    brolls = timeline.get("brolls", [])
    qr_overlays = timeline.get("qr_overlays", [])
    timeline_words = timeline.get("timeline_words", [])

    fps = cfg.fps
    frame_ticks = TICKS_PER_SEC // fps

    # Quantize all segments to exact frames so A/V and captions share identical timing
    aligned_words, seg_frame_ranges = _remap_words_to_frames(segments, timeline_words, fps)

    # Render caption PNGs and QR badge PNGs locally via Pillow
    caption_states, _, _ = render_caption_states(
        timeline_words=aligned_words,
        work_dir=work_dir,
        font_path=cfg.font_path,
        width=cfg.target_width,
        style=cfg.caption_style,
    )

    qr_dir = work_dir / "qr_badges"
    qr_entries: list[dict[str, Any]] = []
    for q_idx, qr_item in enumerate(qr_overlays, start=1):
        badge_path = render_qr_badge(
            url=str(qr_item["url"]),
            label=str(qr_item.get("label") or "المصدر"),
            font_path=cfg.font_path,
            out_path=qr_dir / f"qr_{q_idx:02d}.png",
        )
        with Image.open(badge_path) as im:
            bw, bh = im.size
        qr_entries.append(
            {
                "path": badge_path,
                "w": bw,
                "h": bh,
                "start": float(qr_item["timeline_start"]),
                "end": float(qr_item["timeline_end"]),
            }
        )

    # 1. Create FilmCraft project + 1080x1920 30fps sequence
    _run_fc(fc_exe, ["--save-as", str(fcproj), "exec", "file.newProject", f"name={project_dir.name}"])
    _run_fc(
        fc_exe,
        [
            "--project",
            str(fcproj),
            "--save",
            "exec",
            "file.newSequence",
            "name=Reel",
            f"width={cfg.target_width}",
            f"height={cfg.target_height}",
            f"fps={fps}",
            "sampleRate=48000",
            "video=4",
        ],
    )

    # 2. Import all media assets (source clips, B-rolls, caption PNGs, QR badges)
    all_assets: list[Path] = [Path(s["clip_path"]) for s in segments]
    all_assets.extend(Path(b["path"]) for b in brolls if b.get("path") and Path(b["path"]).is_file())
    all_assets.extend(st.image_path for st in caption_states)
    all_assets.extend(q["path"] for q in qr_entries)
    item_map = _import_in_batches(fc_exe, fcproj, all_assets)

    # 3. Place talking-head clips (V1+A1 linked), B-rolls (V2), Captions (V3), QR (V4)
    place_cmds: list[dict[str, Any]] = []
    seg_meta: list[dict[str, Any]] = []
    broll_meta: list[dict[str, Any]] = []
    cap_meta: list[dict[str, Any]] = []
    qr_meta: list[dict[str, Any]] = []

    for seg, (start_frame, dur_frames) in zip(segments, seg_frame_ranges):
        item_id = item_map[str(Path(seg["clip_path"]).resolve())]
        src_in_ticks = int(round(float(seg["source_start"]) * TICKS_PER_SEC))
        dur_ticks = dur_frames * frame_ticks
        tl_ticks = start_frame * frame_ticks
        place_cmds.append(
            {
                "id": "timeline.place",
                "params": {
                    "item": item_id,
                    "track": "V1",
                    "audioTrack": "A1",
                    "time": tl_ticks,
                    "sourceIn": src_in_ticks,
                    "duration": dur_ticks,
                    "video": True,
                    "audio": True,
                },
            }
        )
        seg_meta.append({"zoom": bool(seg.get("zoom"))})

    for b in brolls:
        b_path = b.get("path")
        if not b_path or not Path(b_path).is_file():
            continue
        item_id = item_map[str(Path(b_path).resolve())]
        b_start_f = max(0, int(round(float(b["timeline_start"]) * fps)))
        b_dur_f = max(1, int(round(float(b["duration"]) * fps)))
        place_cmds.append(
            {
                "id": "timeline.place",
                "params": {
                    "item": item_id,
                    "track": "V2",
                    "time": b_start_f * frame_ticks,
                    "sourceIn": 0,
                    "duration": b_dur_f * frame_ticks,
                    "video": True,
                    "audio": False,
                },
            }
        )
        broll_meta.append({})

    for st in caption_states:
        item_id = item_map[str(st.image_path.resolve())]
        c_start_f = max(0, int(round(st.start * fps)))
        c_end_f = max(c_start_f + 1, int(round(st.end * fps)))
        place_cmds.append(
            {
                "id": "timeline.place",
                "params": {
                    "item": item_id,
                    "track": "V3",
                    "time": c_start_f * frame_ticks,
                    "sourceIn": 0,
                    "duration": (c_end_f - c_start_f) * frame_ticks,
                    "video": True,
                    "audio": False,
                },
            }
        )
        cap_meta.append({})

    for q in qr_entries:
        item_id = item_map[str(q["path"].resolve())]
        q_start_f = max(0, int(round(q["start"] * fps)))
        q_end_f = max(q_start_f + 1, int(round(q["end"] * fps)))
        place_cmds.append(
            {
                "id": "timeline.place",
                "params": {
                    "item": item_id,
                    "track": "V4",
                    "time": q_start_f * frame_ticks,
                    "sourceIn": 0,
                    "duration": (q_end_f - q_start_f) * frame_ticks,
                    "video": True,
                    "audio": False,
                },
            }
        )
        qr_meta.append({"w": q["w"], "h": q["h"]})

    place_results = _run_fc_jsonl(fc_exe, fcproj, place_cmds)
    clip_dims = _extract_clip_anchors(fc_exe, fcproj)

    # 4. Configure scale/position + A1 audio mastering inserts in one batch
    fx_cmds: list[dict[str, Any]] = []
    res_iter = iter(place_results)

    for meta in seg_meta:
        v_clip = int(next(res_iter)["result"]["clips"][0])
        sw, sh = clip_dims.get(v_clip, (float(cfg.target_width), float(cfg.target_height)))
        cover_scale = 100.0 * max(cfg.target_width / max(1.0, sw), cfg.target_height / max(1.0, sh))
        final_scale = round(cover_scale * (cfg.zoom_factor if meta["zoom"] else 1.0), 2)
        fx_cmds.append(
            {
                "id": "effects.setParam",
                "params": {"clip": v_clip, "effect": "motion", "param": "scale", "value": final_scale},
            }
        )

    for _ in broll_meta:
        v_clip = int(next(res_iter)["result"]["clips"][0])
        sw, sh = clip_dims.get(v_clip, (float(cfg.target_width), float(cfg.target_height)))
        cover_scale = round(100.0 * max(cfg.target_width / max(1.0, sw), cfg.target_height / max(1.0, sh)), 2)
        fx_cmds.append(
            {
                "id": "effects.setParam",
                "params": {"clip": v_clip, "effect": "motion", "param": "scale", "value": cover_scale},
            }
        )

    cap_center_y = float(cfg.caption_style.overlay_y) + float(cfg.caption_style.overlay_height) / 2.0
    for _ in cap_meta:
        v_clip = int(next(res_iter)["result"]["clips"][0])
        fx_cmds.append(
            {
                "id": "effects.setParam",
                "params": {
                    "clip": v_clip,
                    "effect": "motion",
                    "param": "position",
                    "value": [cfg.target_width / 2.0, cap_center_y],
                },
            }
        )

    for qm in qr_meta:
        v_clip = int(next(res_iter)["result"]["clips"][0])
        qx = float(cfg.target_width) - 36.0 - float(qm["w"]) / 2.0
        qy = 70.0 + float(qm["h"]) / 2.0
        fx_cmds.append(
            {
                "id": "effects.setParam",
                "params": {
                    "clip": v_clip,
                    "effect": "motion",
                    "param": "position",
                    "value": [qx, qy],
                },
            }
        )

    # Clean, broadcast-level audio inserts directly on linked Track A1
    fx_cmds.append({"id": "mixer.addInsert", "params": {"strip": "A1", "effect": "vocal_enhancer"}})
    fx_cmds.append({"id": "mixer.addInsert", "params": {"strip": "A1", "effect": "dynamics_rack"}})
    _run_fc_jsonl(fc_exe, fcproj, fx_cmds)

    # 5. Export final synchronized MP4
    _run_fc(fc_exe, ["--project", str(fcproj), "export", str(final_mp4), "--format", "h264"])

    ffmpeg_exe = get_ffmpeg_exe(cfg.ffmpeg_path)
    out_info = probe_media(final_mp4, ffmpeg_exe=ffmpeg_exe)
    size_mb = round(final_mp4.stat().st_size / (1024 * 1024), 2)

    return {
        "status": "rendered",
        "backend": "filmcraft",
        "project_path": str(fcproj),
        "output_path": str(final_mp4),
        "duration": round(out_info.duration, 2),
        "resolution": f"{out_info.width}x{out_info.height}",
        "size_mb": size_mb,
    }
