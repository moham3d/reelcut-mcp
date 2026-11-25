"""Edit plan validation, colloquial Arabic word alignment, and 2-line summary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .brolls import load_broll_index
from .config import ReelcutConfig
from .models import EditPlan


def _align_corrected_words(
    corrected_text: str,
    orig_words: list[dict[str, Any]],
    seg_start: float,
    seg_end: float,
    timeline_offset: float,
) -> list[dict[str, Any]]:
    """Map corrected colloquial Arabic words onto timeline timestamps accurately."""
    tokens = [t.strip() for t in corrected_text.split() if t.strip()]
    if not tokens:
        return []

    filtered_orig = [
        w
        for w in orig_words
        if ((w["start"] + w["end"]) / 2.0) >= (seg_start - 0.15)
        and ((w["start"] + w["end"]) / 2.0) <= (seg_end + 0.15)
    ]

    if len(tokens) == len(filtered_orig) and filtered_orig:
        aligned = []
        for tok, orig in zip(tokens, filtered_orig):
            w_start = max(seg_start, min(seg_end, float(orig["start"])))
            w_end = max(w_start + 0.06, min(seg_end, float(orig["end"])))
            aligned.append(
                {
                    "word": tok,
                    "start": round(timeline_offset + (w_start - seg_start), 3),
                    "end": round(timeline_offset + (w_end - seg_start), 3),
                }
            )
        return aligned

    if filtered_orig:
        span_start = max(seg_start, float(filtered_orig[0]["start"]))
        span_end = min(seg_end, float(filtered_orig[-1]["end"]))
        if span_end <= span_start + 0.1:
            span_start, span_end = seg_start, seg_end
    else:
        span_start, span_end = seg_start, seg_end

    total_dur = max(0.2, span_end - span_start)
    weights = [max(2, len(t)) for t in tokens]
    weight_sum = sum(weights)

    aligned = []
    cursor = span_start
    for tok, weight in zip(tokens, weights):
        dur = total_dur * (weight / weight_sum)
        w_start = cursor
        w_end = min(span_end, cursor + dur)
        aligned.append(
            {
                "word": tok,
                "start": round(timeline_offset + (w_start - seg_start), 3),
                "end": round(timeline_offset + (w_end - seg_start), 3),
            }
        )
        cursor = w_end

    if aligned:
        aligned[-1]["end"] = round(timeline_offset + (span_end - seg_start), 3)
    return aligned


def format_two_line_summary(
    plan: EditPlan,
    total_duration: float,
    broll_count: int,
    zoom_count: int,
    pending_queries: list[str],
    qr_count: int = 0,
) -> str:
    """Build a concise 2-line Arabic summary for user approval before rendering."""
    broll_note = f"{broll_count} B-roll" if broll_count else "بدون B-rolls"
    if pending_queries:
        broll_note += f" (منهم {len(pending_queries)} بحث مقترح: {', '.join(pending_queries[:3])})"
    qr_note = f" + {qr_count} QR للمصادر" if qr_count else ""
    line1 = (
        f"الخطة: {len(plan.segments)} مقطع بعد حذف الفواصل والتكرارات "
        f"| المدة المتوقعة: {total_duration:.1f} ثانية | Zoom-in على {zoom_count} مقطع."
    )
    line2 = (
        f"الـ B-Rolls والكابشن: {broll_note}{qr_note} مع Captions عامية ديناميكية (2-4 كلمات). "
        f"أوافق وأبدأ الريندر؟"
    )
    return f"{line1}\n{line2}"


def save_edit_plan(
    project_dir: str | Path,
    plan_data: dict[str, Any] | EditPlan,
) -> dict[str, Any]:
    """Validate an edit plan, resolve word timings & B-rolls, and return a 2-line summary."""
    proj = Path(project_dir).resolve()
    work_dir = proj / "work"
    words_file = work_dir / "words_detail.json"
    if not words_file.is_file():
        raise FileNotFoundError(
            f"Missing {words_file}. Run prepare_project first before saving an edit plan."
        )

    plan = plan_data if isinstance(plan_data, EditPlan) else EditPlan.model_validate(plan_data)
    words_detail = json.loads(words_file.read_text(encoding="utf-8"))
    clips_meta = words_detail.get("clips", {})
    segments_meta = words_detail.get("segments", {})

    # Group all Whisper words by clip_id so custom start/end ranges work smoothly
    words_by_clip: dict[str, list[dict[str, Any]]] = {}
    for seg_info in segments_meta.values():
        cid = seg_info["clip_id"]
        words_by_clip.setdefault(cid, []).extend(seg_info.get("words", []))
    for cid in words_by_clip:
        words_by_clip[cid].sort(key=lambda w: w["start"])

    cfg = ReelcutConfig.load(proj)
    broll_index = {
        b["broll_id"]: b
        for b in load_broll_index(proj, cfg.brolls_dir)
    }

    resolved_segments: list[dict[str, Any]] = []
    all_timeline_words: list[dict[str, Any]] = []
    resolved_brolls: list[dict[str, Any]] = []
    qr_overlays: list[dict[str, Any]] = []
    pending_queries: list[str] = []

    timeline_cursor = 0.0
    zoom_count = 0

    for idx, seg in enumerate(plan.segments, start=1):
        if seg.clip_id not in clips_meta:
            raise ValueError(f"Unknown clip_id '{seg.clip_id}' in segment {idx}.")
        if seg.end <= seg.start:
            raise ValueError(f"Segment {idx} has end <= start ({seg.end} <= {seg.start}).")

        seg_dur = round(seg.end - seg.start, 3)
        clip_info = clips_meta[seg.clip_id]

        aligned_words = _align_corrected_words(
            seg.corrected_text,
            words_by_clip.get(seg.clip_id, []),
            seg.start,
            seg.end,
            timeline_cursor,
        )
        all_timeline_words.extend(aligned_words)

        if seg.zoom:
            zoom_count += 1

        broll_entry: dict[str, Any] | None = None
        if seg.broll:
            b_start = round(timeline_cursor + min(seg.broll.offset_sec, max(0.0, seg_dur - 0.3)), 3)
            b_dur = round(min(seg.broll.duration_sec, max(0.3, (timeline_cursor + seg_dur) - b_start)), 3)

            resolved_path: str | None = None
            if seg.broll.path and Path(seg.broll.path).is_file():
                resolved_path = str(Path(seg.broll.path).resolve())
            elif seg.broll.broll_id and seg.broll.broll_id in broll_index:
                resolved_path = broll_index[seg.broll.broll_id]["path"]
            elif seg.broll.query:
                pending_queries.append(seg.broll.query)

            broll_entry = {
                "broll_id": seg.broll.broll_id,
                "path": resolved_path,
                "query": seg.broll.query,
                "timeline_start": b_start,
                "duration": b_dur,
            }
            if resolved_path:
                resolved_brolls.append(broll_entry)

        resolved_segments.append(
            {
                "index": idx,
                "clip_id": seg.clip_id,
                "clip_path": clip_info["path"],
                "enhanced_wav": clip_info["enhanced_wav"],
                "source_start": round(seg.start, 3),
                "source_end": round(seg.end, 3),
                "timeline_start": round(timeline_cursor, 3),
                "timeline_end": round(timeline_cursor + seg_dur, 3),
                "duration": seg_dur,
                "zoom": seg.zoom,
                "corrected_text": seg.corrected_text,
                "broll": broll_entry,
                "qr_url": seg.qr_url,
            }
        )
        if seg.qr_url:
            qr_overlays.append(
                {
                    "url": seg.qr_url,
                    "label": seg.qr_label or "المصدر",
                    "timeline_start": round(timeline_cursor, 3),
                    "timeline_end": round(timeline_cursor + seg_dur, 3),
                }
            )
        timeline_cursor = round(timeline_cursor + seg_dur, 3)

    (work_dir / "edit_plan.json").write_text(
        json.dumps(plan.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    resolved_payload = {
        "total_duration": timeline_cursor,
        "color_preset": plan.color_preset,
        "segments": resolved_segments,
        "timeline_words": all_timeline_words,
        "brolls": resolved_brolls,
        "qr_overlays": qr_overlays,
        "pending_broll_queries": pending_queries,
    }
    (work_dir / "timeline_resolved.json").write_text(
        json.dumps(resolved_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    summary_2_lines = format_two_line_summary(
        plan=plan,
        total_duration=timeline_cursor,
        broll_count=len(resolved_brolls) + len(pending_queries),
        zoom_count=zoom_count,
        pending_queries=pending_queries,
        qr_count=len(qr_overlays),
    )

    return {
        "status": "plan_saved_awaiting_approval",
        "total_duration": timeline_cursor,
        "segment_count": len(resolved_segments),
        "broll_count": len(resolved_brolls),
        "qr_count": len(qr_overlays),
        "pending_broll_queries": pending_queries,
        "summary_2_lines": summary_2_lines,
        "plan_path": str(work_dir / "edit_plan.json"),
    }
