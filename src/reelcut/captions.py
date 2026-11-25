"""Dynamic 2-4 word Arabic RTL caption renderer (Pillow + FFmpeg concat overlay)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import arabic_reshaper
from bidi.algorithm import get_display
from PIL import Image, ImageDraw, ImageFont

from .config import CaptionStyle


@dataclass(slots=True)
class CaptionState:
    """Single visual caption state across a timeline duration."""

    start: float
    end: float
    image_path: Path


def _shape_arabic_word(word: str) -> str:
    """Reshape an Arabic word and apply BiDi visual order for Pillow BASIC layout."""
    reshaped = arabic_reshaper.reshape(word)
    return get_display(reshaped)


def _chunk_words(
    words: list[dict[str, Any]],
    target_size: int = 3,
    min_size: int = 2,
    max_size: int = 4,
) -> list[list[dict[str, Any]]]:
    """Group timeline words into balanced 2-4 word chunks without awkward pauses."""
    if not words:
        return []

    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []

    for w in words:
        if current:
            gap = float(w["start"]) - float(current[-1]["end"])
            if len(current) >= max_size or (len(current) >= min_size and gap >= 0.45):
                chunks.append(current)
                current = []
        current.append(w)
        if len(current) == target_size:
            chunks.append(current)
            current = []

    if current:
        if len(current) == 1 and chunks and len(chunks[-1]) < max_size:
            chunks[-1].append(current[0])
        else:
            chunks.append(current)

    return chunks


def _load_font(font_path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(
            font_path,
            size=size,
            layout_engine=ImageFont.Layout.BASIC,
        )
    except TypeError:
        return ImageFont.truetype(font_path, size=size)


def _render_chunk_frame(
    chunk: list[dict[str, Any]],
    active_idx: int,
    width: int,
    style: CaptionStyle,
    font: ImageFont.FreeTypeFont,
    out_path: Path,
) -> Path:
    """Render one transparent RGBA caption overlay PNG with RTL word highlighting."""
    img = Image.new("RGBA", (width, style.overlay_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    visual_words = [_shape_arabic_word(str(w["word"])) for w in chunk]
    space_bbox = draw.textbbox((0, 0), " ", font=font, stroke_width=style.stroke_width)
    space_w = max(14, (space_bbox[2] - space_bbox[0]) + 6)

    word_boxes = [
        draw.textbbox((0, 0), vw, font=font, stroke_width=style.stroke_width)
        for vw in visual_words
    ]
    word_widths = [max(1, box[2] - box[0]) for box in word_boxes]
    line_height = max((box[3] - box[1]) for box in word_boxes)

    total_text_w = sum(word_widths) + space_w * max(0, len(visual_words) - 1)

    # Auto-shrink slightly if a 4-word chunk is wider than safe mobile margins
    safe_max_w = width - 140
    active_font = font
    if total_text_w > safe_max_w and total_text_w > 0:
        scale_ratio = safe_max_w / total_text_w
        shrunk_size = max(36, int(style.font_size * scale_ratio))
        active_font = _load_font(font.path, shrunk_size)
        space_bbox = draw.textbbox((0, 0), " ", font=active_font, stroke_width=style.stroke_width)
        space_w = max(12, (space_bbox[2] - space_bbox[0]) + 5)
        word_boxes = [
            draw.textbbox((0, 0), vw, font=active_font, stroke_width=style.stroke_width)
            for vw in visual_words
        ]
        word_widths = [max(1, box[2] - box[0]) for box in word_boxes]
        line_height = max((box[3] - box[1]) for box in word_boxes)
        total_text_w = sum(word_widths) + space_w * max(0, len(visual_words) - 1)

    center_x = width // 2
    center_y = style.overlay_height // 2

    pill_left = center_x - (total_text_w // 2) - style.pill_padding_x
    pill_right = center_x + (total_text_w // 2) + style.pill_padding_x
    pill_top = center_y - (line_height // 2) - style.pill_padding_y
    pill_bottom = center_y + (line_height // 2) + style.pill_padding_y

    draw.rounded_rectangle(
        [pill_left, pill_top, pill_right, pill_bottom],
        radius=style.pill_radius,
        fill=style.pill_bg_color,
    )

    # Draw logical words Right-to-Left so spoken order matches Arabic reading order
    cursor_right = center_x + (total_text_w // 2)
    text_y = center_y - (line_height // 2)

    for idx, (vw, w_width, box) in enumerate(zip(visual_words, word_widths, word_boxes)):
        draw_x = cursor_right - w_width - box[0]
        draw_y = text_y - box[1]
        color = style.highlight_color if idx == active_idx else style.text_color
        draw.text(
            (draw_x, draw_y),
            vw,
            font=active_font,
            fill=color,
            stroke_width=style.stroke_width,
            stroke_fill=style.stroke_color,
        )
        cursor_right -= w_width + space_w

    img.save(out_path, format="PNG")
    return out_path


def render_caption_states(
    timeline_words: list[dict[str, Any]],
    work_dir: Path,
    font_path: str,
    width: int = 1080,
    style: CaptionStyle | None = None,
) -> tuple[list[CaptionState], Path, Path]:
    """Render dynamic 2-4 word Arabic RTL caption PNG states and return them."""
    cfg_style = style or CaptionStyle()
    captions_dir = work_dir / "captions_frames"
    captions_dir.mkdir(parents=True, exist_ok=True)

    blank_png = captions_dir / "blank.png"
    Image.new("RGBA", (width, cfg_style.overlay_height), (0, 0, 0, 0)).save(blank_png, format="PNG")

    font = _load_font(font_path, cfg_style.font_size)
    chunks = _chunk_words(
        timeline_words,
        target_size=cfg_style.words_per_chunk,
        min_size=cfg_style.min_words_per_chunk,
        max_size=cfg_style.max_words_per_chunk,
    )

    states: list[CaptionState] = []
    state_counter = 0

    for chunk in chunks:
        for word_idx, w in enumerate(chunk):
            w_start = max(0.0, float(w["start"]))
            if word_idx + 1 < len(chunk):
                w_end = max(w_start + 0.04, float(chunk[word_idx + 1]["start"]))
            else:
                w_end = max(w_start + 0.04, float(w["end"]))

            if states and w_start < states[-1].end:
                w_start = states[-1].end
            if w_end <= w_start + 0.02:
                continue

            state_counter += 1
            png_path = captions_dir / f"cap_{state_counter:04d}.png"
            _render_chunk_frame(chunk, word_idx, width, cfg_style, font, png_path)
            states.append(CaptionState(start=w_start, end=w_end, image_path=png_path))

    return states, blank_png, captions_dir


def build_caption_concat_manifest(
    timeline_words: list[dict[str, Any]],
    total_duration: float,
    work_dir: Path,
    font_path: str,
    width: int = 1080,
    style: CaptionStyle | None = None,
) -> Path:
    """Render dynamic caption PNG states and write an FFmpeg concat manifest."""
    states, blank_png, captions_dir = render_caption_states(
        timeline_words=timeline_words,
        work_dir=work_dir,
        font_path=font_path,
        width=width,
        style=style,
    )

    # Build continuous timeline concat manifest covering [0, total_duration]
    manifest_lines = ["ffconcat version 1.0"]
    cursor = 0.0
    last_png = blank_png

    def _emit(img_file: Path, dur: float) -> None:
        nonlocal last_png
        if dur <= 0.005:
            return
        safe_path = str(img_file.resolve()).replace("\\", "/")
        manifest_lines.append(f"file '{safe_path}'")
        manifest_lines.append(f"duration {dur:.4f}")
        last_png = img_file

    for st in states:
        if st.start > cursor + 0.02:
            _emit(blank_png, st.start - cursor)
            cursor = st.start
        dur = max(0.03, st.end - cursor)
        _emit(st.image_path, dur)
        cursor += dur

    if cursor < total_duration:
        _emit(blank_png, total_duration - cursor)

    # FFmpeg concat demuxer requires the final file listed twice without duration
    final_safe = str(last_png.resolve()).replace("\\", "/")
    manifest_lines.append(f"file '{final_safe}'")

    manifest_path = captions_dir / "captions_concat.txt"
    manifest_path.write_text("\n".join(manifest_lines), encoding="utf-8")
    return manifest_path


def render_qr_badge(
    url: str,
    label: str,
    font_path: str,
    out_path: Path,
    qr_box_size: int = 4,
) -> Path:
    """Render a compact dark-pill QR code badge with Arabic label for corner overlay."""
    import qrcode

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=qr_box_size,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")

    qr_w, qr_h = qr_img.size
    pad = 12
    label_h = 34
    badge_w = qr_w + pad * 2
    badge_h = qr_h + pad * 2 + label_h

    badge = Image.new("RGBA", (badge_w, badge_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(badge)
    draw.rounded_rectangle(
        [0, 0, badge_w - 1, badge_h - 1],
        radius=16,
        fill=(15, 15, 20, 195),
        outline=(255, 255, 255, 65),
        width=2,
    )
    badge.paste(qr_img, (pad, pad))

    if label.strip():
        font = _load_font(font_path, 22)
        visual_label = _shape_arabic_word(label.strip())
        bbox = draw.textbbox((0, 0), visual_label, font=font)
        lw = bbox[2] - bbox[0]
        lh = bbox[3] - bbox[1]
        tx = (badge_w - lw) // 2 - bbox[0]
        ty = pad + qr_h + ((label_h - lh) // 2) - bbox[1]
        draw.text((tx, ty), visual_label, font=font, fill=(255, 214, 10, 255))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    badge.save(out_path, format="PNG")
    return out_path
