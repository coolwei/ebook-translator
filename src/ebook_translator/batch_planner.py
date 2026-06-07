from __future__ import annotations

from dataclasses import dataclass

from .logging_setup import get_logger
from .models import Segment


@dataclass
class TranslationBatch:
    segments: list[Segment]
    batch_index: int


def plan_translation_batches(
    segments: list[Segment],
    *,
    segments_per_request: int,
    max_chars_per_request: int,
    preserve_segment_order: bool = True,
) -> list[TranslationBatch]:
    """Group segments into API batches respecting count and char limits."""
    if segments_per_request < 1:
        raise ValueError("segments_per_request must be >= 1")

    logger = get_logger()
    ordered = list(segments) if preserve_segment_order else list(segments)
    batches: list[TranslationBatch] = []
    current: list[Segment] = []
    current_chars = 0
    batch_index = 0

    def _flush() -> None:
        nonlocal current, current_chars, batch_index
        if not current:
            return
        batch_index += 1
        batches.append(TranslationBatch(segments=list(current), batch_index=batch_index))
        current = []
        current_chars = 0

    for seg in ordered:
        seg_chars = len(seg.source_text)
        if seg_chars > max_chars_per_request:
            if current:
                _flush()
            logger.warning(
                "Segment %s exceeds max_chars_per_request (%d > %d); sending alone",
                seg.segment_id,
                seg_chars,
                max_chars_per_request,
            )
            batch_index += 1
            batches.append(TranslationBatch(segments=[seg], batch_index=batch_index))
            continue

        would_exceed_count = len(current) >= segments_per_request
        would_exceed_chars = current and current_chars + seg_chars > max_chars_per_request
        if would_exceed_count or would_exceed_chars:
            _flush()

        current.append(seg)
        current_chars += seg_chars

    _flush()
    return batches