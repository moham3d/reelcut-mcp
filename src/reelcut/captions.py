"""Dynamic 2-4 word Arabic RTL caption renderer (Pillow + FFmpeg concat overlay)."""

from __future__ import annotations

import base64
import html
import io
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

    if style.pill_bg_color[3] > 0:
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


def _font_data_uri(font_path: str) -> str:
    """Embed TTF/OTF as base64 data URI so headless Edge/Chrome never blocks file://."""
    raw = Path(font_path).read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    mime = "font/otf" if font_path.lower().endswith(".otf") else "font/ttf"
    return f"data:{mime};base64,{b64}"


def _render_states_with_browser(
    jobs: list[tuple[list[dict[str, Any]], int, Path]],
    width: int,
    style: CaptionStyle,
    font_path: str,
) -> bool:
    """Render Arabic RTL caption states using Edge/Chrome HarfBuzz shaping."""
    if not jobs:
        return True
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return False

    font_uri = _font_data_uri(font_path)
    bg_css = (
        f"rgba({style.pill_bg_color[0]}, {style.pill_bg_color[1]}, {style.pill_bg_color[2]}, {style.pill_bg_color[3] / 255:.3f})"
        if style.pill_bg_color[3] > 0
        else "transparent"
    )
    template_html = f"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8" />
<style>
@font-face {{
  font-family: 'ReelCutFont';
  src: url('{font_uri}');
  font-weight: 100 900;
  font-style: normal;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
html, body {{
  width: {width}px;
  height: {style.overlay_height}px;
  background: transparent;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: 'ReelCutFont', 'Tahoma', sans-serif;
}}
.pill {{
  display: inline-flex;
  flex-direction: row;
  flex-wrap: nowrap;
  align-items: center;
  justify-content: center;
  gap: 18px;
  max-width: {width - 110}px;
  padding: {style.pill_padding_y}px {style.pill_padding_x}px;
  border-radius: {style.pill_radius}px;
  background: {bg_css};
  direction: rtl;
  unicode-bidi: isolate;
}}
.w {{
  font-size: {style.font_size}px;
  font-weight: 700;
  line-height: 1.25;
  color: rgb({style.text_color[0]}, {style.text_color[1]}, {style.text_color[2]});
  -webkit-text-stroke: {style.stroke_width * 2}px rgb({style.stroke_color[0]}, {style.stroke_color[1]}, {style.stroke_color[2]});
  paint-order: stroke fill;
  filter: drop-shadow(0 5px 10px rgba(0,0,0,0.9)) drop-shadow(0 2px 4px rgba(0,0,0,0.85));
  white-space: nowrap;
}}
.w.active {{
  color: rgb({style.highlight_color[0]}, {style.highlight_color[1]}, {style.highlight_color[2]});
  transform: scale(1.07);
}}
</style>
</head>
<body><div id="pill" class="pill"></div></body>
</html>"""

    try:
        with sync_playwright() as p:
            browser = None
            for channel in ("msedge", "chrome"):
                try:
                    browser = p.chromium.launch(channel=channel, headless=True)
                    break
                except Exception:
                    continue
            if browser is None:
                return False

            page = browser.new_page(viewport={"width": width, "height": style.overlay_height})
            page.set_content(template_html, wait_until="load")
            page.evaluate("() => document.fonts.ready")

            for chunk, word_idx, png_path in jobs:
                spans = []
                for idx, w in enumerate(chunk):
                    cls = "w active" if idx == word_idx else "w"
                    spans.append(f'<span class="{cls}">{html.escape(str(w["word"]))}</span>')
                inner = "".join(spans)
                page.evaluate(
                    """(markup) => {
                        const el = document.getElementById('pill');
                        el.innerHTML = markup;
                        el.style.transform = 'scale(1)';
                        const maxW = window.innerWidth - 110;
                        if (el.scrollWidth > maxW && el.scrollWidth > 0) {
                            el.style.transform = `scale(${(maxW / el.scrollWidth).toFixed(3)})`;
                        }
                    }""",
                    inner,
                )
                page.screenshot(path=str(png_path), omit_background=True)
            browser.close()
        return True
    except Exception:
        return False


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

    chunks = _chunk_words(
        timeline_words,
        target_size=cfg_style.words_per_chunk,
        min_size=cfg_style.min_words_per_chunk,
        max_size=cfg_style.max_words_per_chunk,
    )

    states: list[CaptionState] = []
    jobs: list[tuple[list[dict[str, Any]], int, Path]] = []
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
            jobs.append((chunk, word_idx, png_path))
            states.append(CaptionState(start=w_start, end=w_end, image_path=png_path))

    if not _render_states_with_browser(jobs, width, cfg_style, font_path):
        font = _load_font(font_path, cfg_style.font_size)
        for chunk, word_idx, png_path in jobs:
            _render_chunk_frame(chunk, word_idx, width, cfg_style, font, png_path)

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
        rendered_with_browser = False
        try:
            from playwright.sync_api import sync_playwright

            buf = io.BytesIO()
            qr_img.save(buf, format="PNG")
            qr_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            font_uri = _font_data_uri(font_path)
            badge_html = f"""<!DOCTYPE html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8" />
<style>
@font-face {{ font-family: 'ReelCutFont'; src: url('{font_uri}'); font-weight: 100 900; }}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
html, body {{ width: {badge_w}px; height: {badge_h}px; background: transparent; overflow: hidden; }}
.card {{
  width: {badge_w}px; height: {badge_h}px;
  background: rgba(15, 15, 20, 0.78);
  border: 2px solid rgba(255, 255, 255, 0.25);
  border-radius: 16px;
  display: flex; flex-direction: column; align-items: center; justify-content: flex-start;
  padding-top: {pad}px; gap: 4px;
}}
img {{ width: {qr_w}px; height: {qr_h}px; display: block; border-radius: 6px; }}
.lbl {{
  font-family: 'ReelCutFont', 'Tahoma', sans-serif;
  font-size: 22px; font-weight: 700; color: #FFD60A; line-height: 1.2;
}}
</style></head>
<body><div class="card"><img src="data:image/png;base64,{qr_b64}" /><div class="lbl">{html.escape(label.strip())}</div></div></body>
</html>"""
            with sync_playwright() as p:
                for channel in ("msedge", "chrome"):
                    try:
                        browser = p.chromium.launch(channel=channel, headless=True)
                        page = browser.new_page(viewport={"width": badge_w, "height": badge_h})
                        page.set_content(badge_html, wait_until="load")
                        page.evaluate("() => document.fonts.ready")
                        out_path.parent.mkdir(parents=True, exist_ok=True)
                        page.screenshot(path=str(out_path), omit_background=True)
                        browser.close()
                        rendered_with_browser = True
                        break
                    except Exception:
                        continue
        except Exception:
            rendered_with_browser = False

        if rendered_with_browser:
            return out_path

        # Fallback: use Tahoma/Arial bold for Pillow BASIC shaping if browser unavailable
        fallback_font_path = font_path
        for cand in (r"C:\Windows\Fonts\tahomabd.ttf", r"C:\Windows\Fonts\arialbd.ttf"):
            if Path(cand).is_file() and "Playpen" in Path(font_path).name:
                fallback_font_path = cand
                break
        font = _load_font(fallback_font_path, 22)
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
