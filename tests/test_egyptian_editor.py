"""Tests for the Egyptian-dialect editor methods (words.py + wiring).

Regression case: a plan segment ending at raw 53.61s chopped the word
"مصري" (53.58-53.90) in half — audible in the render as a missing word while
the caption still showed it. The pipeline must now snap every cut to a word
boundary, repair Whisper's dialect mishearings against the script, flag
retakes, and never render tofu captions.
"""

from __future__ import annotations

import json
from pathlib import Path

from reelcut.captions import resolve_pil_font
from reelcut.config import (
    find_default_arabic_font,
    font_supports_arabic_presentation_forms,
)
from reelcut.planner import save_edit_plan
from reelcut.words import (
    correct_with_script,
    flag_retakes,
    sanitize_word_timings,
    snap_segment_to_corrected_words,
    snap_segment_to_words,
)

# Real Whisper word timings from the raw reel around "في دكتور مصري اسمه".
MASRI_WORDS = [
    {"word": "في", "start": 53.18, "end": 53.28, "prob": 0.64},
    {"word": "دكتور", "start": 53.28, "end": 53.58, "prob": 0.95},
    {"word": "مصر", "start": 53.58, "end": 53.90, "prob": 0.99},
    {"word": "اسمه", "start": 53.90, "end": 54.16, "prob": 1.0},
]

SCRIPT = (
    "الإزازة اللي ما بتشربهاش ممكن تكون البوكسر اللي انت لابسه دلوقتي. "
    "بيفرموها، بيسيحوها، وبيغزلوها بوليستر وبيعملوا منها ملابس داخلية. "
    "في التسعينات في دكتور مصري اسمه أحمد شفيق. "
    "لبس كلاب بوكسرات بوليستر لمدة سنتين. "
    "عدد الحيوانات المنوية وقع والتشوهات زادت. "
    "والبس قطن ولا حاجة تغيرت عندهم. "
    "عشان كده اللي بتلبسه تحت هدومك على طول لازم تبقى ضامن إنه على الأقل "
    "94% قطن زي كوتونيل ميرسيرزيه. ستاي سيف."
)


def test_snap_extends_end_inside_word_masri() -> None:
    """The regression: segment 53.11-53.61 cut 'مصري' (53.58-53.90) in half.
    The edge extends to the word's end, capped at the next word's start so
    it never eats into the following word."""
    new_start, new_end, adjustments = snap_segment_to_words(53.11, 53.61, MASRI_WORDS)
    assert new_start == 53.11
    assert new_end == 53.90  # word end 53.90; tail capped at "اسمه" starting 53.90
    assert len(adjustments) == 1
    assert adjustments[0]["word"] == "مصر"
    assert adjustments[0]["edge"] == "end"
    assert adjustments[0]["reason"] == "end_inside_word"


def test_snap_extends_start_inside_word() -> None:
    words = [
        {"word": "بيفرموها", "start": 26.52, "end": 27.44, "prob": 0.7},
        {"word": "يسيحوها", "start": 27.44, "end": 28.22, "prob": 0.8},
    ]
    new_start, new_end, adjustments = snap_segment_to_words(27.0, 28.5, words)
    assert new_start == 26.46  # word start 26.52 - 0.06 lead
    assert new_end == 28.5
    assert adjustments[0]["edge"] == "start"


def test_snap_leaves_boundaries_in_silence() -> None:
    new_start, new_end, adjustments = snap_segment_to_words(51.0, 53.0, MASRI_WORDS)
    assert (new_start, new_end) == (51.0, 53.0)
    assert adjustments == []


def test_snap_no_words_leaves_segment_unchanged() -> None:
    assert snap_segment_to_words(1.0, 2.0, []) == (1.0, 2.0, [])


def test_snap_never_eats_into_next_word() -> None:
    """Whisper sometimes splits one spoken word into two timed words
    ('مصري' heard as 'طريق' 53.28-53.64 + 'مصير' 53.64-53.90). The
    boundary-only snap stops at the next word's start instead of eating
    into it; the corrected-text snap (below) is what crosses the split."""
    words = [
        {"word": "في", "start": 53.12, "end": 53.28, "prob": 0.6},
        {"word": "طريق", "start": 53.28, "end": 53.64, "prob": 0.4},
        {"word": "مصير", "start": 53.64, "end": 53.90, "prob": 0.9},
        {"word": "اسمه", "start": 53.90, "end": 54.16, "prob": 1.0},
    ]
    new_start, new_end, adjustments = snap_segment_to_words(53.11, 53.61, words)
    assert new_end == 53.64  # end of "طريق", capped at "مصير" starting 53.64
    assert len(adjustments) == 1
    assert adjustments[0]["word"] == "طريق"


def test_snap_to_corrected_words_ignores_earlier_rehearsal() -> None:
    """The raw clip says the hook three times. A segment near the third take
    must snap to that take, never drag the cut back to the first rehearsal."""
    words = [
        {"word": "الإزازة", "start": 1.48, "end": 2.36, "prob": 0.6},
        {"word": "اللي", "start": 2.36, "end": 2.40, "prob": 0.8},
        {"word": "الإزازة", "start": 18.62, "end": 19.18, "prob": 0.6},
        {"word": "التي", "start": 19.18, "end": 19.26, "prob": 0.8},
        {"word": "تشربها", "start": 19.52, "end": 19.98, "prob": 0.6},
        {"word": "البوكسر", "start": 20.56, "end": 21.02, "prob": 0.7},
        {"word": "دلوقتي", "start": 21.52, "end": 21.92, "prob": 0.8},
    ]
    new_start, new_end, _ = snap_segment_to_corrected_words(
        18.72, 21.90, "الإزازة اللي تشربها البوكسر دلوقتي", words
    )
    assert new_start >= 18.0  # snapped to the 18.62 take, not the 1.48 one
    assert new_start == 18.56
    assert new_end == 22.0  # last word ends 21.92 + 0.08 tail


def test_snap_to_corrected_words_crosses_split_word() -> None:
    """The segment is supposed to say 'في دكتور مصري'. Even though Whisper
    split 'مصري' into 'طريق' + 'مصير', the snap must end after 'مصير'."""
    words = [
        {"word": "في", "start": 53.12, "end": 53.28, "prob": 0.6},
        {"word": "طريق", "start": 53.28, "end": 53.64, "prob": 0.4},
        {"word": "مصير", "start": 53.64, "end": 53.90, "prob": 0.9},
        {"word": "اخساه", "start": 53.90, "end": 54.38, "prob": 0.7},
    ]
    new_start, new_end, adjustments = snap_segment_to_corrected_words(
        53.11, 53.61, "في دكتور مصري", words
    )
    assert new_start == 53.11
    assert new_end == 53.90
    assert adjustments and adjustments[0]["word"] == "مصير"


def test_sanitize_caps_absurd_word_duration() -> None:
    words = [
        {"word": "الإزازة", "start": 3.28, "end": 6.86, "prob": 0.5},
        {"word": "اللي", "start": 6.90, "end": 7.10, "prob": 0.9},
    ]
    cleaned, fixed = sanitize_word_timings(words)
    assert cleaned[0]["end"] - cleaned[0]["start"] <= 1.5 + 1e-9
    assert fixed >= 1


def test_sanitize_resolves_overlaps() -> None:
    words = [
        {"word": "واحد", "start": 1.0, "end": 1.6, "prob": 0.9},
        {"word": "اتنين", "start": 1.4, "end": 1.9, "prob": 0.9},
    ]
    cleaned, _ = sanitize_word_timings(words)
    assert cleaned[0]["end"] <= cleaned[1]["start"]


def test_correct_with_script_fixes_mishearings() -> None:
    turn = [
        {"word": "البكسر", "start": 20.56, "end": 21.02, "prob": 0.7},
        {"word": "اللي", "start": 21.02, "end": 21.12, "prob": 0.86},
        {"word": "انت", "start": 21.12, "end": 21.52, "prob": 0.48},
        {"word": "لبسه", "start": 21.52, "end": 21.92, "prob": 0.77},
        {"word": "دلوقتي", "start": 21.92, "end": 22.32, "prob": 0.78},
    ]
    corrected, corrections = correct_with_script(turn, SCRIPT)
    texts = [w["word"] for w in corrected]
    assert "البوكسر" in texts
    assert "لابسه" in texts
    assert "البكسر" not in texts
    assert {c["original"] for c in corrections} == {"البكسر", "لبسه"}
    # Timings are preserved, only the text changes.
    assert corrected[0]["start"] == 20.56 and corrected[0]["end"] == 21.02


def test_correct_with_script_fixes_masri() -> None:
    turn = [
        {"word": "في", "start": 53.18, "end": 53.28, "prob": 0.64},
        {"word": "دكتور", "start": 53.28, "end": 53.58, "prob": 0.95},
        {"word": "مصر", "start": 53.58, "end": 53.90, "prob": 0.99},
        {"word": "اسمه", "start": 53.90, "end": 54.16, "prob": 1.0},
    ]
    corrected, corrections = correct_with_script(turn, SCRIPT)
    assert [w["word"] for w in corrected] == ["في", "دكتور", "مصري", "اسمه"]
    assert corrections[0]["original"] == "مصر"


def test_correct_with_script_keeps_improvisation() -> None:
    # A line the speaker improvised: no script line matches, nothing changes.
    turn = [
        {"word": "يا", "start": 1.0, "end": 1.2, "prob": 0.9},
        {"word": "جماعة", "start": 1.2, "end": 1.6, "prob": 0.9},
        {"word": "ركزوا", "start": 1.6, "end": 2.0, "prob": 0.9},
        {"word": "معايا", "start": 2.0, "end": 2.4, "prob": 0.9},
    ]
    corrected, corrections = correct_with_script(turn, SCRIPT)
    assert corrected == turn
    assert corrections == []


def test_flag_retakes_keeps_cleanest_delivery() -> None:
    def _seg(sid, start, probs):
        return {
            "id": sid,
            "text": "بيفرموها بيسيحوها",
            "start": start,
            "words": [
                {"word": "بيفرموها", "start": start, "end": start + 0.5, "prob": p}
                for p in probs
            ],
        }

    segments = [
        _seg("c01_s01", 26.0, [0.5, 0.6]),  # rehearsal, shaky
        _seg("c01_s02", 34.0, [0.9, 0.95]),  # cleanest delivery
        _seg("c01_s03", 41.0, [0.7, 0.8]),  # another take
    ]
    retakes = flag_retakes(segments)
    assert retakes == {"c01_s01": "c01_s02", "c01_s03": "c01_s02"}


def test_bundled_fonts_lack_presentation_forms() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    playpen = repo_root / "assets" / "fonts" / "PlaypenSansArabic[wght].ttf"
    markazi = repo_root / "assets" / "fonts" / "MarkaziText[wght].ttf"
    assert playpen.is_file() and markazi.is_file()
    assert not font_supports_arabic_presentation_forms(str(playpen))
    assert not font_supports_arabic_presentation_forms(str(markazi))


def test_default_font_covers_presentation_forms() -> None:
    font_path = find_default_arabic_font()
    assert font_supports_arabic_presentation_forms(font_path)


def test_resolve_pil_font_avoids_tofu_font() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    playpen = str(repo_root / "assets" / "fonts" / "PlaypenSansArabic[wght].ttf")
    resolved = resolve_pil_font(playpen, "ﻣﺼﺮﻱ")
    assert resolved != playpen
    assert font_supports_arabic_presentation_forms(resolved)


def test_save_edit_plan_snaps_masri_segment(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    work_dir = proj / "work"
    (proj / "clips").mkdir(parents=True)
    work_dir.mkdir(parents=True)
    clip = proj / "clips" / "c01.mp4"
    clip.write_bytes(b"fake")
    words_detail = {
        "clips": {"c01": {"path": str(clip), "enhanced_wav": str(work_dir / "c01.wav")}},
        "segments": {
            "c01_s01": {
                "clip_id": "c01",
                "clip_path": str(clip),
                "enhanced_wav": str(work_dir / "c01.wav"),
                "start": 51.0,
                "end": 55.0,
                "words": MASRI_WORDS,
            }
        },
    }
    (work_dir / "words_detail.json").write_text(
        json.dumps(words_detail, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    result = save_edit_plan(
        proj,
        {
            "title": "تجربة",
            "segments": [
                {
                    "clip_id": "c01",
                    "start": 53.11,
                    "end": 53.61,
                    "corrected_text": "في دكتور مصري",
                    "zoom": False,
                }
            ],
        },
    )
    assert result["status"] == "plan_saved_awaiting_approval"
    assert result["boundary_adjustments"], "expected the chopped word to be fixed"
    assert result["boundary_adjustments"][0]["word"] == "مصر"
    resolved = json.loads((work_dir / "timeline_resolved.json").read_text(encoding="utf-8"))
    assert resolved["segments"][0]["source_end"] == 53.90
    assert resolved["boundary_adjustments"][0]["new"] == 53.90
