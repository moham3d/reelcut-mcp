"""Word-level methods that make ReelCut listen, cut, and edit like a human.

A human editor never cuts in the middle of a word, never trusts a misheard
word when they know the script, and never keeps three takes of the same line.
The pipeline used to do all three: plan boundaries were raw floats from
correlation/VAD, Whisper's Egyptian-dialect mishearings went straight into
the transcript, and retakes were left for the director to spot by hand.

This module is the fix:

* :func:`sanitize_word_timings` — Whisper's word timestamps are noisy for
  fast colloquial Arabic (a single "word" can span 3.5s). Clamp them to
  plausible speech before anything downstream trusts them.
* :func:`snap_segment_to_words` — move a segment's edges to the nearest word
  boundary so a cut can never split a word in half. This is the "مصري" fix:
  a segment ending at 53.61 while the word "مصري" runs 53.58-53.90 must be
  extended to 53.98, not left chopping the word.
* :func:`correct_with_script` — anchor the transcription to the script the
  speaker was reading. Words Whisper misheard (البكسر / البوكسر، قط / قطن،
  مصر / مصري، كتنيل / كوتونيل) are repaired only where the script line
  matches the spoken turn closely, so real improvisation is left alone.
* :func:`flag_retakes` — group identical lines and keep the cleanest delivery
  (highest mean word probability), flagging the rest as retakes.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

# A single spoken word longer than this is a Whisper timing error, not speech.
MAX_WORD_DURATION = 1.5
MIN_WORD_DURATION = 0.05
# Air to keep around a snapped word so the cut never clips its onset/offset.
SNAP_LEAD = 0.06
SNAP_TAIL = 0.08
# Inside-word tolerance: a boundary within 20ms of a word edge counts as on it.
EDGE_TOLERANCE = 0.02

_ARABIC_DIACRITICS = re.compile(r"[\u064b-\u0652\u0670\u0640]")
_ARABIC_LETTER_NORMALIZATION = str.maketrans(
    {
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ٱ": "ا",
        "ة": "ه",
        "ى": "ي",
        "ؤ": "و",
        "ئ": "ي",
    }
)
_NON_WORD = re.compile(r"[^\w\u0600-\u06ff]+")


def normalize_arabic_token(token: str) -> str:
    """Normalize an Arabic token for comparison (not for display).

    Strips diacritics/tatweel, unifies alef forms, teh-marbuta, alef-maqsura,
    and punctuation, so "البُوكسر" and "البوكسر" compare equal.
    """
    token = _ARABIC_DIACRITICS.sub("", token or "")
    token = token.translate(_ARABIC_LETTER_NORMALIZATION)
    token = _NON_WORD.sub("", token)
    return token.strip().lower()


def sanitize_word_timings(
    words: list[dict[str, Any]],
    max_word_duration: float = MAX_WORD_DURATION,
) -> tuple[list[dict[str, Any]], int]:
    """Clean Whisper word timings into plausible, non-overlapping spans.

    Drops empty words, sorts by start, caps absurd durations, and resolves
    overlaps by trimming the earlier word. Returns (cleaned_words, n_fixed).
    """
    cleaned: list[dict[str, Any]] = []
    for w in words:
        text = (w.get("word") or "").strip()
        if not text:
            continue
        try:
            start = float(w["start"])
            end = float(w["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end <= start:
            end = start + MIN_WORD_DURATION
        cleaned.append({**w, "word": text, "start": start, "end": end})

    cleaned.sort(key=lambda w: (w["start"], w["end"]))
    fixed = 0
    result: list[dict[str, Any]] = []
    for w in cleaned:
        if w["end"] - w["start"] > max_word_duration:
            w = {**w, "end": round(w["start"] + max_word_duration, 3)}
            fixed += 1
        if result and w["start"] < result[-1]["end"]:
            prev = result[-1]
            if w["start"] - prev["start"] >= MIN_WORD_DURATION:
                result[-1] = {**prev, "end": round(w["start"], 3)}
                fixed += 1
            else:
                fixed += 1
                continue
        result.append(w)
    return result, fixed


def snap_segment_to_words(
    start: float,
    end: float,
    words: list[dict[str, Any]],
    lead: float = SNAP_LEAD,
    tail: float = SNAP_TAIL,
) -> tuple[float, float, list[dict[str, Any]]]:
    """Snap a segment's edges to word boundaries; never split a word.

    If ``start`` falls strictly inside a word, the segment is extended back
    to that word's start (minus a small lead, floored at the previous word's
    end so it never eats into the previous word). If ``end`` falls strictly
    inside a word, it is extended forward to that word's end (plus a small
    tail, capped at the next word's start). Boundaries that already sit in
    silence are left alone.

    Returns ``(new_start, new_end, adjustments)`` where each adjustment
    records the word, the edge, the reason, and old/new values.
    """
    if not words:
        return round(start, 3), round(end, 3), []

    ordered = sorted(words, key=lambda w: (float(w["start"]), float(w["end"])))
    new_start, new_end = float(start), float(end)
    adjustments: list[dict[str, Any]] = []

    for i, w in enumerate(ordered):
        ws, we = float(w["start"]), float(w["end"])
        if ws + EDGE_TOLERANCE < new_start < we - EDGE_TOLERANCE:
            candidate = ws - lead
            if i > 0:
                candidate = max(candidate, float(ordered[i - 1]["end"]))
            new_start = max(0.0, candidate)
            adjustments.append(
                {
                    "word": w["word"],
                    "edge": "start",
                    "reason": "start_inside_word",
                    "old": round(start, 3),
                    "new": round(new_start, 3),
                }
            )
            break

    for i, w in enumerate(ordered):
        ws, we = float(w["start"]), float(w["end"])
        if ws + EDGE_TOLERANCE < new_end < we - EDGE_TOLERANCE:
            candidate = we + tail
            if i + 1 < len(ordered):
                candidate = min(candidate, float(ordered[i + 1]["start"]))
            new_end = candidate
            adjustments.append(
                {
                    "word": w["word"],
                    "edge": "end",
                    "reason": "end_inside_word",
                    "old": round(end, 3),
                    "new": round(new_end, 3),
                }
            )
            break

    if new_end <= new_start:
        return round(start, 3), round(end, 3), []
    return round(new_start, 3), round(new_end, 3), adjustments


def snap_segment_to_corrected_words(
    start: float,
    end: float,
    corrected_text: str,
    orig_words: list[dict[str, Any]],
    lead: float = SNAP_LEAD,
    tail: float = SNAP_TAIL,
) -> tuple[float, float, list[dict[str, Any]]]:
    """Snap a segment so every word it is supposed to say is fully audible.

    The director's ``corrected_text`` says what the clip should say; the
    Whisper words say when each word was spoken. Match the two (the same
    two-pass alignment as :func:`correct_with_script`), then extend the
    segment to cover the first and last matched word — never past the
    neighbouring word's edge. This is the real "مصري" fix: a segment meant
    to say "في دكتور مصري" must end after "مصري" finishes (53.90), even
    when Whisper split that word into two timed fragments.

    Falls back to :func:`snap_segment_to_words` when nothing matches.
    """
    tokens = [t for t in (corrected_text or "").split() if t.strip()]
    if not tokens or not orig_words:
        return snap_segment_to_words(start, end, orig_words, lead, tail)

    ordered_all = sorted(orig_words, key=lambda w: (float(w["start"]), float(w["end"])))
    # Only words near the segment can be what it is supposed to say. Without
    # this window the alignment would match a rehearsal 15 seconds away —
    # the raw clip says the hook three times — and drag the cut backwards.
    margin = 0.5
    ordered = [
        w
        for w in ordered_all
        if (float(w["start"]) + float(w["end"])) / 2.0 >= start - margin
        and (float(w["start"]) + float(w["end"])) / 2.0 <= end + margin
    ]
    if not ordered:
        return snap_segment_to_words(start, end, orig_words, lead, tail)

    turn_norm = [normalize_arabic_token(t) for t in tokens]
    line_norm = [normalize_arabic_token(w["word"]) for w in ordered]

    matched: dict[int, int] = {}  # turn token index -> orig word index
    matcher = difflib.SequenceMatcher(a=turn_norm, b=line_norm, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                matched[i1 + k] = j1 + k
        elif tag == "replace" and (i2 - i1) == (j2 - j1):
            for k in range(i2 - i1):
                if (
                    difflib.SequenceMatcher(None, turn_norm[i1 + k], line_norm[j1 + k]).ratio()
                    >= 0.6
                ):
                    matched[i1 + k] = j1 + k
    used = set(matched.values())
    for i, tok in enumerate(turn_norm):
        if i in matched or tok in line_norm:
            continue
        best_j, best_sim = -1, 0.0
        for j, lt in enumerate(line_norm):
            if j in used or not lt:
                continue
            sim = difflib.SequenceMatcher(None, tok, lt).ratio()
            if sim > best_sim:
                best_j, best_sim = j, sim
        if best_j >= 0 and best_sim >= 0.75:
            matched[i] = best_j
            used.add(best_j)

    if not matched:
        return snap_segment_to_words(start, end, orig_words, lead, tail)

    first_idx = min(matched.values())
    last_idx = max(matched.values())
    first, last = ordered[first_idx], ordered[last_idx]
    new_start, new_end = float(start), float(end)
    adjustments: list[dict[str, Any]] = []

    # Caps come from the full word list: never eat into the neighbouring
    # word, even when it sits outside the matching window.
    first_pos = ordered_all.index(first)
    last_pos = ordered_all.index(last)

    first_start = float(first["start"])
    if new_start > first_start:
        candidate = first_start - lead
        if first_pos > 0:
            candidate = max(candidate, float(ordered_all[first_pos - 1]["end"]))
        new_start = max(0.0, candidate)
        adjustments.append(
            {
                "word": first["word"],
                "edge": "start",
                "reason": "start_before_first_word",
                "old": round(start, 3),
                "new": round(new_start, 3),
            }
        )

    last_end = float(last["end"])
    if new_end < last_end:
        candidate = last_end + tail
        if last_pos + 1 < len(ordered_all):
            candidate = min(candidate, float(ordered_all[last_pos + 1]["start"]))
        new_end = candidate
        adjustments.append(
            {
                "word": last["word"],
                "edge": "end",
                "reason": "end_before_last_word",
                "old": round(end, 3),
                "new": round(new_end, 3),
            }
        )

    if new_end <= new_start:
        return round(start, 3), round(end, 3), []
    return round(new_start, 3), round(new_end, 3), adjustments


def _script_lines(script_text: str) -> list[list[str]]:
    """Split the script into candidate lines (one spoken line each)."""
    lines: list[list[str]] = []
    for raw in re.split(r"[\n.!؟?]+", script_text or ""):
        tokens = [t for t in raw.split() if t.strip()]
        if len(tokens) >= 2:
            lines.append(tokens)
    return lines


def correct_with_script(
    turn_words: list[dict[str, Any]],
    script_text: str | None,
    min_line_overlap: float = 0.5,
    min_similarity: float = 0.6,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Repair Whisper mishearings using the script the speaker was reading.

    Finds the script line that best matches the spoken turn, aligns the two
    token sequences, and replaces a transcribed word with the script word
    only when the two are close (similarity >= ``min_similarity``) — the
    signature of a mishearing (البكسر/البوكسر، قط/قطن، مصر/مصري), not a
    real deviation, which differs by a whole word. Timings and probabilities
    are kept; only the word text changes.

    Returns ``(corrected_words, corrections)``.
    """
    if not turn_words or not script_text:
        return list(turn_words), []

    turn_tokens = [normalize_arabic_token(w["word"]) for w in turn_words]
    turn_tokens = [t for t in turn_tokens if t]
    if not turn_tokens:
        return list(turn_words), []

    best_line: list[str] | None = None
    best_score = 0.0
    for line in _script_lines(script_text):
        line_norm = [normalize_arabic_token(t) for t in line]
        overlap = len(set(turn_tokens) & set(line_norm)) / max(1, len(set(turn_tokens)))
        if overlap > best_score:
            best_score = overlap
            best_line = line

    if best_line is None or best_score < min_line_overlap:
        return list(turn_words), []

    line_norm = [normalize_arabic_token(t) for t in best_line]
    matcher = difflib.SequenceMatcher(a=turn_tokens, b=line_norm, autojunk=False)
    corrected = [dict(w) for w in turn_words]
    corrections: list[dict[str, Any]] = []
    used_line: set[int] = set()

    def _replace(idx: int, line_idx: int, similarity: float) -> None:
        original = corrected[idx]["word"]
        corrected[idx] = {**corrected[idx], "word": best_line[line_idx]}
        used_line.add(line_idx)
        corrections.append(
            {
                "original": original,
                "corrected": best_line[line_idx],
                "similarity": round(similarity, 2),
                "prob": corrected[idx].get("prob"),
            }
        )

    # Pass A: local alignment. Equal-length "replace" blocks are mishearings
    # of the words in the same position (مصر/مصري، لبسه/لابسه).
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "replace" or (i2 - i1) != (j2 - j1):
            continue
        for k in range(i2 - i1):
            heard = turn_tokens[i1 + k]
            intended = line_norm[j1 + k]
            if heard == intended or not intended:
                used_line.add(j1 + k)
                continue
            similarity = difflib.SequenceMatcher(None, heard, intended).ratio()
            if similarity >= min_similarity:
                _replace(i1 + k, j1 + k, similarity)
    # Mark exact matches as used so pass B cannot steal them.
    for i, tok in enumerate(turn_tokens):
        if tok in line_norm and corrected[i]["word"] == turn_words[i]["word"]:
            for j, lt in enumerate(line_norm):
                if lt == tok and j not in used_line:
                    used_line.add(j)
                    break

    # Pass B: global rescue. Speakers skip words, so a misheard word's true
    # counterpart may sit far from its position (البكسر at the start of the
    # turn matches البوكسر in the middle of the line). Search the whole line
    # for the closest unused word, with a stricter threshold.
    for i, tok in enumerate(turn_tokens):
        if corrected[i]["word"] != turn_words[i]["word"]:
            continue  # already corrected
        if tok in line_norm:
            continue  # heard correctly; nothing to fix
        best_j, best_sim = -1, 0.0
        for j, lt in enumerate(line_norm):
            if j in used_line or not lt:
                continue
            sim = difflib.SequenceMatcher(None, tok, lt).ratio()
            if sim > best_sim:
                best_j, best_sim = j, sim
        if best_j >= 0 and best_sim >= 0.75:
            _replace(i, best_j, best_sim)

    return corrected, corrections


def flag_retakes(
    segments: list[dict[str, Any]],
) -> dict[str, str]:
    """Flag repeated lines, keeping the cleanest delivery.

    ``segments`` are dicts with ``id``, ``text`` and ``words`` (each word
    optionally carrying ``prob``). Segments whose normalized text matches are
    one line said several times; the one with the highest mean word
    probability wins (ties go to the latest take, usually the most practiced).
    Returns ``{retake_id: best_id}``.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for seg in segments:
        key = " ".join(
            t for t in (normalize_arabic_token(tok) for tok in (seg.get("text") or "").split()) if t
        )
        if key:
            groups.setdefault(key, []).append(seg)

    retakes: dict[str, str] = {}
    for group in groups.values():
        if len(group) < 2:
            continue

        def _score(seg: dict[str, Any]) -> tuple[float, float]:
            probs = [float(w["prob"]) for w in seg.get("words") or [] if w.get("prob") is not None]
            mean_prob = sum(probs) / len(probs) if probs else 0.0
            return (mean_prob, float(seg.get("start") or 0.0))

        best = max(group, key=_score)
        for seg in group:
            if seg["id"] != best["id"]:
                retakes[seg["id"]] = best["id"]
    return retakes
