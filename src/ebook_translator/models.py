from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class Segment(BaseModel):
    segment_id: str
    chapter_index: int
    block_index: int
    sha1_prefix: str
    source_text: str
    source_html: str
    tag_name: str
    chapter_href: str


class TranslationRecord(BaseModel):
    segment_id: str
    source_hash: str
    status: Literal["completed", "failed", "quality_failed"]
    source: str
    translation: str
    model: str
    attempt: int
    error: str | None = None
    # Set when this record reused a cached translation from another segment with
    # the same source_hash (no provider call was made).
    reused_from_segment_id: str | None = None
    fallback_from: str | None = None
    fallback_attempt: int | None = None
    # Structured match details from quality checks (e.g. simplified_chinese positions).
    quality_matches: list | None = None
    created_at: datetime


class JobState(BaseModel):
    job_id: str
    input_path: str
    output_dir: str
    status: Literal["running", "completed", "failed", "interrupted"]
    total_segments: int
    completed_segments: int
    failed_segments: int
    created_at: datetime
    updated_at: datetime
