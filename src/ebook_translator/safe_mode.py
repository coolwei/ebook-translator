from __future__ import annotations

import json
import logging
import math
from pathlib import Path

logger = logging.getLogger(__name__)

from .checkpoint import CheckpointManager
from .config import AppConfig
from .validator import classify_failure

RATE_LIMIT_KEYWORDS = ("rate limit", "429", "openai_error")


def is_rate_limit_error(error: str | None) -> bool:
    if not error:
        return False
    if classify_failure(error) == "rate limit":
        return True
    low = error.lower()
    return any(kw in low for kw in RATE_LIMIT_KEYWORDS)


def translation_record_line_count(job_dir: Path) -> int:
    path = job_dir / "translations.jsonl"
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def count_rate_limit_errors_since(job_dir: Path, since_line: int) -> int:
    path = job_dir / "translations.jsonl"
    if not path.exists():
        return 0
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    count = 0
    for line in lines[since_line:]:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("Skipping corrupt JSONL line in %s", path)
            continue
        if rec.get("status") == "failed" and is_rate_limit_error(rec.get("error")):
            count += 1
    return count


def pending_segment_count(job_dir: Path, cfg: AppConfig, segment_count: int) -> int:
    checkpoint = CheckpointManager(job_dir)
    segments = checkpoint.load_segments()
    if not segments:
        return segment_count

    completed = checkpoint.load_completed_ids()
    failed = checkpoint.load_failed_ids()
    # quality_failed segments are retried by the post-batch workflow, not the main
    # batch loop. Excluding them prevents an infinite loop when quality keeps failing.
    quality_failed = checkpoint.load_quality_failed_ids()
    retry_failed = cfg.resume.retry_failed
    return sum(
        1
        for seg in segments
        if seg.segment_id not in completed
        and seg.segment_id not in quality_failed
        and (retry_failed or seg.segment_id not in failed)
    )


def compute_effective_segments(
    segment_count: int,
    max_segments: int,
    limit: int | None,
) -> int | None:
    """Return None when the book would be skipped for exceeding max_segments."""
    if segment_count > max_segments:
        return None
    effective = segment_count
    if limit is not None:
        effective = min(effective, limit)
    return effective


def estimate_batch_count(effective_segments: int, batch_size: int) -> int:
    if batch_size <= 0:
        return 1
    if effective_segments <= 0:
        return 0
    return math.ceil(effective_segments / batch_size)


def batch_limit_for_run(batch_size: int, remaining_limit: int | None) -> int | None:
    if remaining_limit is not None and remaining_limit <= 0:
        return None
    if remaining_limit is None:
        return batch_size
    return min(batch_size, remaining_limit)
