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
    status: Literal["completed", "failed"]
    source: str
    translation: str
    model: str
    attempt: int
    error: str | None = None
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
