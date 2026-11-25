"""Final 9:16 video rendering with FFmpeg (segments, zoom-in, B-rolls, Arabic captions)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .captions import build_caption_concat_manifest, render_qr_badge
from .config import ReelcutConfig
from .media import get_ffmpeg_exe, probe_media, run_ffmpeg


def _escape_filter_path(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").replace(":", r"\:")


FFMPEG_COLOR_PRESETS: dict[str, str] = {
    "clean": "eq=contrast=1.06:saturation=1.06,unsharp=3:3:0.4",
    "warm": "colorbalance=rs=0.04:gs=0.01:bs=-0.04,eq=contrast=1.05:saturation=1.08",
    "cinematic": "eq=contrast=1.10:brightness=-0.02:saturation=0.95,vignette=angle=PI/5",
    "crisp": "eq=contrast=1.08:saturation=1.04,unsharp=5:5:0.7",
    "bw": "hue=s=0,eq=contrast=1.12",
}


def _render_base_talking_head(
    segments: list[dict[str, Any]],
    out_mp4: Path,
    cfg: ReelcutConfig,
    ffmpeg_exe: str,
    filter_dir: Path,
    color_preset: str = "none",
) -> Path:
    """Trim, crop to 9:16, apply optional 1.12x zoom, and concat talking-head segments."""
    args: list[str] = []
    filter_lines: list[str] = []
    concat_inputs: list[str] = []

    w, h, fps = cfg.target_width, cfg.target_height, cfg.fps
    zoom_w = int(round(w * cfg.zoom_factor))
    zoom_h = int(round(h * cfg.zoom_factor))
    # Ensure even dimensions for yuv420p
    zoom_w += zoom_w % 2
    zoom_h += zoom_h % 2

    input_idx = 0
    for seg in segments:
        s_start = float(seg["source_start"])
        s_end = float(seg["source_end"])
        dur = max(0.05, s_end - s_start)

        # Keep video and audio locked from the SAME source clip (never separate WAV/MP4 seek!)
        args.extend(["-ss", f"{s_start:.3f}", "-t", f"{dur:.3f}", "-i", seg["clip_path"]])
        src_in = input_idx
        input_idx += 1

        v_label = f"v{seg['index']}"
        a_label = f"a{seg['index']}"

        color_vf = FFMPEG_COLOR_PRESETS.get(color_preset.strip().lower(), "")
        color_suffix = f",{color_vf}" if color_vf else ""

        if seg.get("zoom"):
            v_chain = (
                f"[{src_in}:v]fps={fps},"
                f"scale={zoom_w}:{zoom_h}:force_original_aspect_ratio=increase,"
                f"crop={w}:{h}{color_suffix},setsar=1,setpts=PTS-STARTPTS[{v_label}]"
            )
        else:
            v_chain = (
                f"[{src_in}:v]fps={fps},"
                f"scale={w}:{h}:force_original_aspect_ratio=increase,"
                f"crop={w}:{h}{color_suffix},setsar=1,setpts=PTS-STARTPTS[{v_label}]"
            )

        a_chain = (
            f"[{src_in}:a]highpass=f=80,lowpass=f=14000,afftdn=nf=-25:tn=1,"
            f"acompressor=threshold=-18dB:ratio=3:attack=5:release=60:makeup=2dB,"
            f"loudnorm=I=-14:TP=-1.5:LRA=11,"
            f"aresample=48000,asetpts=PTS-STARTPTS[{a_label}]"
        )
        filter_lines.append(v_chain)
        filter_lines.append(a_chain)
        concat_inputs.append(f"[{v_label}][{a_label}]")

    n = len(segments)
    filter_lines.append(f"{''.join(concat_inputs)}concat=n={n}:v=1:a=1[vout][aout]")

    graph = ";".join(filter_lines)
    script_path = filter_dir / "base_concat.ffscript"
    script_path.write_text(";\n".join(filter_lines), encoding="utf-8")

    fc_flag = "-/filter_complex" if len(graph) > 12000 else "-filter_complex"
    fc_val = str(script_path) if len(graph) > 12000 else graph

    args.extend(
        [
            fc_flag,
            fc_val,
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(out_mp4),
        ]
    )
    run_ffmpeg(args, ffmpeg_exe=ffmpeg_exe)
    return out_mp4


def render_video(project_dir: str | Path, backend: str | None = None) -> dict[str, Any]:
    """Render final 9:16 MP4 via FilmCraft (preferred when installed) or FFmpeg fallback."""
    proj = Path(project_dir).resolve()
    work_dir = proj / "work"
    timeline_file = work_dir / "timeline_resolved.json"
    if not timeline_file.is_file():
        raise FileNotFoundError(
            f"Missing {timeline_file}. Run save_edit_plan first before rendering."
        )

    cfg = ReelcutConfig.load(proj)
    chosen_backend = (backend or cfg.render_backend or "auto").lower()
    timeline = json.loads(timeline_file.read_text(encoding="utf-8"))

    segments = timeline.get("segments", [])
    if not segments:
        raise ValueError("Timeline contains no segments to render.")

    if chosen_backend in {"auto", "filmcraft"} and cfg.filmcraft_path:
        from .filmcraft import render_with_filmcraft

        return render_with_filmcraft(proj, timeline, cfg)

    ffmpeg_exe = get_ffmpeg_exe(cfg.ffmpeg_path)

    total_duration = float(timeline["total_duration"])
    brolls = timeline.get("brolls", [])
    qr_overlays = timeline.get("qr_overlays", [])
    timeline_words = timeline.get("timeline_words", [])

    # 1. Build base talking-head video with enhanced audio + occasional zoom-in + color preset
    base_mp4 = work_dir / "base_talking_head.mp4"
    preset_name = str(timeline.get("color_preset") or cfg.color_preset or "none")
    _render_base_talking_head(segments, base_mp4, cfg, ffmpeg_exe, work_dir, color_preset=preset_name)

    # 2. Generate dynamic Arabic RTL word-highlighted caption overlay stream
    captions_manifest = build_caption_concat_manifest(
        timeline_words=timeline_words,
        total_duration=total_duration,
        work_dir=work_dir,
        font_path=cfg.font_path,
        width=cfg.target_width,
        style=cfg.caption_style,
    )

    # 3. Composite B-rolls + Captions in one final pass
    output_dir = proj / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    final_mp4 = output_dir / "final_reel.mp4"

    args: list[str] = ["-i", str(base_mp4)]
    filter_lines: list[str] = []
    current_v = "[0:v]"
    next_input = 1

    w, h, fps = cfg.target_width, cfg.target_height, cfg.fps
    image_exts = {".jpg", ".jpeg", ".png", ".webp"}

    for idx, broll in enumerate(brolls, start=1):
        b_path = Path(broll["path"])
        if not b_path.is_file():
            continue
        b_start = float(broll["timeline_start"])
        b_dur = float(broll["duration"])
        b_end = b_start + b_dur

        if b_path.suffix.lower() in image_exts:
            args.extend(["-loop", "1", "-t", f"{b_dur:.3f}", "-i", str(b_path)])
        else:
            args.extend(["-stream_loop", "-1", "-t", f"{b_dur:.3f}", "-i", str(b_path)])

        b_label = f"br{idx}"
        out_label = f"vb{idx}"
        filter_lines.append(
            f"[{next_input}:v]fps={fps},"
            f"scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},setsar=1,"
            f"setpts=PTS-STARTPTS+{b_start:.3f}/TB[{b_label}]"
        )
        filter_lines.append(
            f"{current_v}[{b_label}]overlay=0:0:"
            f"enable='between(t,{b_start:.3f},{b_end:.3f})':eof_action=pass[{out_label}]"
        )
        current_v = f"[{out_label}]"
        next_input += 1

    # Add captions stream via concat demuxer
    args.extend(["-f", "concat", "-safe", "0", "-i", str(captions_manifest)])
    cap_idx = next_input
    next_input += 1
    filter_lines.append(f"[{cap_idx}:v]fps={fps},format=rgba[cap]")
    out_tag = "[vfinal]" if not qr_overlays else "[vcap]"
    filter_lines.append(
        f"{current_v}[cap]overlay=0:{cfg.caption_style.overlay_y}:eof_action=pass:shortest=0{out_tag}"
    )
    current_v = out_tag

    # Overlay small corner QR code badges for source citations
    qr_dir = work_dir / "qr_badges"
    for q_idx, qr_item in enumerate(qr_overlays, start=1):
        badge_path = render_qr_badge(
            url=str(qr_item["url"]),
            label=str(qr_item.get("label") or "المصدر"),
            font_path=cfg.font_path,
            out_path=qr_dir / f"qr_{q_idx:02d}.png",
        )
        q_start = float(qr_item["timeline_start"])
        q_end = float(qr_item["timeline_end"])
        args.extend(["-loop", "1", "-t", f"{total_duration:.3f}", "-i", str(badge_path)])
        q_out = "[vfinal]" if q_idx == len(qr_overlays) else f"[vq{q_idx}]"
        filter_lines.append(
            f"{current_v}[{next_input}:v]overlay=W-w-36:70:"
            f"enable='between(t,{q_start:.3f},{q_end:.3f})':eof_action=pass{q_out}"
        )
        current_v = q_out
        next_input += 1

    final_graph = ";".join(filter_lines)
    final_script = work_dir / "final_composite.ffscript"
    final_script.write_text(";\n".join(filter_lines), encoding="utf-8")

    fc_flag = "-/filter_complex" if len(final_graph) > 12000 else "-filter_complex"
    fc_val = str(final_script) if len(final_graph) > 12000 else final_graph

    args.extend(
        [
            fc_flag,
            fc_val,
            "-map",
            "[vfinal]",
            "-map",
            "0:a",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(final_mp4),
        ]
    )
    run_ffmpeg(args, ffmpeg_exe=ffmpeg_exe)

    out_info = probe_media(final_mp4, ffmpeg_exe=ffmpeg_exe)
    size_mb = round(final_mp4.stat().st_size / (1024 * 1024), 2)

    return {
        "status": "rendered",
        "output_path": str(final_mp4),
        "duration": round(out_info.duration, 2),
        "resolution": f"{out_info.width}x{out_info.height}",
        "size_mb": size_mb,
    }
