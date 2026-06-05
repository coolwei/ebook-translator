"""missing_report.py — generate a missing_translation_report.json.

Walks the original spine documents, identifies every translatable text block,
and cross-references it against:
  * segments.jsonl  — was the block extracted by the segmenter?
  * translations.jsonl — was a translation record produced?

Produces a JSON report that classifies each un-bilingualled block so the
caller knows whether to re-segment, retry-failed, or retry-quality-failed.
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

from .epub.reader import SpineDocument
from .models import Segment, TranslationRecord
from .segmenter.segmenter import iter_translatable_blocks


# ---------------------------------------------------------------------------
# Reason codes (for the report's "reason" field)
# ---------------------------------------------------------------------------

REASON_NO_SEGMENT = "no_segment_extracted"        # block never entered segments.jsonl
REASON_FAILED = "translation_failed"              # segment exists but status=failed
REASON_QUALITY_FAILED = "quality_failed"          # segment exists but status=quality_failed
REASON_MISSING_RECORD = "no_translation_record"   # segment exists, no record at all
REASON_OK = "translated"                          # has a completed translation (source-only due to render path issue)
REASON_UNKNOWN = "unknown"


def build_missing_translation_report(
    spine_docs: list[SpineDocument],
    segments: list[Segment],
    translations: dict[str, TranslationRecord],
) -> list[dict[str, Any]]:
    """Return a list of dicts, one per *missing or failed* translatable block.

    Only blocks that did NOT end up as a proper bilingual-block are included —
    i.e. blocks whose translation is absent, failed, or quality-failed.
    Fully translated blocks are omitted from the list (they're not missing).
    """
    # Build lookup: (href, block_index) -> Segment
    seg_by_pos: dict[tuple[str, int], Segment] = {
        (s.chapter_href, s.block_index): s for s in segments
    }
    # Build lookup by segment_id -> latest TranslationRecord
    # (translations dict already holds the latest per segment_id)

    report: list[dict[str, Any]] = []

    for doc in spine_docs:
        soup = BeautifulSoup(doc.content, "lxml")
        for block_index, tag, text in iter_translatable_blocks(soup):
            seg = seg_by_pos.get((doc.href, block_index))
            has_segment = seg is not None

            if has_segment:
                assert seg is not None
                record = translations.get(seg.segment_id)
                has_record = record is not None

                if record is not None and record.status == "completed" and record.translation.strip():
                    # Successfully translated — not missing
                    continue

                # Classify reason
                if record is None:
                    reason = REASON_MISSING_RECORD
                elif record.status == "failed":
                    reason = REASON_FAILED
                elif record.status == "quality_failed":
                    reason = REASON_QUALITY_FAILED
                else:
                    reason = REASON_OK  # completed but empty — treated as unknown issue
            else:
                has_record = False
                record = None
                reason = REASON_NO_SEGMENT

            entry: dict[str, Any] = {
                "chapter_href": doc.href,
                "block_index": block_index,
                "tag_name": tag.name,
                "source_text": text,
                "has_segment": has_segment,
                "segment_id": seg.segment_id if seg else None,
                "has_translation_record": has_record,
                "translation_status": record.status if record else None,
                "error": record.error if record else None,
                "reason": reason,
            }
            report.append(entry)

    return report


def save_missing_translation_report(
    report: list[dict[str, Any]],
    output_dir: Path,
) -> Path:
    """Write *report* to ``missing_translation_report.json`` in *output_dir*."""
    out = output_dir / "missing_translation_report.json"
    out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out
