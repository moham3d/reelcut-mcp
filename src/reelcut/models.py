"""Pydantic schemas for token-efficient AI planning and local state."""

from __future__ import annotations

from pydantic import BaseModel, Field


class WordTiming(BaseModel):
    """Word-level speech timestamp stored locally on disk."""

    word: str
    start: float
    end: float


class CompactSegment(BaseModel):
    """Minimal segment summary exposed to the LLM to minimize token usage."""

    id: str
    clip_id: str
    start: float
    end: float
    text: str
    # Set when this segment repeats a line said better elsewhere: the id of
    # the best delivery of the same line. A human editor drops retakes; the
    # director reads this and does the same.
    retake_of: str | None = None


class ClipSummary(BaseModel):
    """Basic metadata about a raw input clip."""

    clip_id: str
    filename: str
    duration: float
    width: int
    height: int


class BRollItem(BaseModel):
    """Indexed local or cached B-roll item."""

    broll_id: str
    path: str
    tags: list[str] = Field(default_factory=list)
    duration: float = 0.0
    width: int = 0
    height: int = 0
    source: str = "local"


class BRollPlacement(BaseModel):
    """Optional B-roll overlay attached to a planned talking-head segment."""

    broll_id: str | None = None
    path: str | None = None
    query: str | None = None
    offset_sec: float = Field(default=0.0, ge=0.0)
    duration_sec: float = Field(default=2.5, gt=0.2)


class PlannedSegment(BaseModel):
    """Single timeline segment chosen by the AI director."""

    clip_id: str
    start: float = Field(ge=0.0)
    end: float = Field(gt=0.0)
    corrected_text: str
    zoom: bool = False
    broll: BRollPlacement | None = None
    qr_url: str | None = None
    qr_label: str = "المصدر"


class EditPlan(BaseModel):
    """Complete edit decision list returned by the AI director."""

    title: str = ""
    summary_ar: str = ""
    color_preset: str | None = None
    segments: list[PlannedSegment] = Field(min_length=1)
