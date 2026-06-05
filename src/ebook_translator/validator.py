from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .config import QualityConfig
from .models import Segment, TranslationRecord


@dataclass
class ValidationIssue:
    segment_id: str
    check: str
    severity: Literal["warning", "error"]
    detail: str


@dataclass
class ValidationReport:
    total_checked: int
    issues: list[ValidationIssue]
    passed: int
    warnings: int
    errors: int


def _count_html_tags(html: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for m in re.finditer(r"<([a-zA-Z][a-zA-Z0-9]*)[^>]*>", html):
        tag = m.group(1).lower()
        counts[tag] = counts.get(tag, 0) + 1
    return counts


def _extract_urls(text: str) -> set[str]:
    return set(re.findall(r"https?://\S+", text))


def check_empty(segment: Segment, record: TranslationRecord) -> ValidationIssue | None:
    if not record.translation.strip():
        return ValidationIssue(
            segment_id=segment.segment_id,
            check="empty_translation",
            severity="error",
            detail="Translation is empty.",
        )
    return None


def check_identical(segment: Segment, record: TranslationRecord) -> ValidationIssue | None:
    src = re.sub(r"\s+", " ", segment.source_text).strip().lower()
    trg = re.sub(r"\s+", " ", record.translation).strip().lower()
    if src == trg:
        return ValidationIssue(
            segment_id=segment.segment_id,
            check="identical_translation",
            severity="warning",
            detail="Translation is identical to source text.",
        )
    return None


def check_length_ratio(
    segment: Segment, record: TranslationRecord, max_ratio: float
) -> ValidationIssue | None:
    src_len = len(segment.source_text.strip())
    trg_len = len(record.translation.strip())
    if src_len == 0:
        return None
    ratio = trg_len / src_len
    if ratio > max_ratio:
        return ValidationIssue(
            segment_id=segment.segment_id,
            check="length_ratio",
            severity="warning",
            detail=f"Translation is {ratio:.1f}x longer than source (max {max_ratio}x).",
        )
    return None


def check_html_integrity(segment: Segment, record: TranslationRecord) -> ValidationIssue | None:
    src_tags = _count_html_tags(segment.source_html)
    trg_tags = _count_html_tags(record.translation)
    if src_tags != trg_tags:
        return ValidationIssue(
            segment_id=segment.segment_id,
            check="html_integrity",
            severity="warning",
            detail=f"HTML tag mismatch. Source: {src_tags}, Translation: {trg_tags}.",
        )
    return None


def check_missing_urls(segment: Segment, record: TranslationRecord) -> ValidationIssue | None:
    src_urls = _extract_urls(segment.source_html)
    trg_urls = _extract_urls(record.translation)
    missing = src_urls - trg_urls
    if missing:
        return ValidationIssue(
            segment_id=segment.segment_id,
            check="missing_urls",
            severity="warning",
            detail=f"URLs in source not found in translation: {', '.join(sorted(missing))}",
        )
    return None


def validate_translations(
    pairs: list[tuple[Segment, TranslationRecord]],
    config: QualityConfig,
) -> ValidationReport:
    issues: list[ValidationIssue] = []

    for segment, record in pairs:
        if record.status != "completed":
            continue

        if config.validate_empty_translation:
            issue = check_empty(segment, record)
            if issue:
                issues.append(issue)
                continue  # Skip further checks if empty

        if config.validate_untranslated_ratio:
            issue = check_identical(segment, record)
            if issue:
                issues.append(issue)

        issue = check_length_ratio(segment, record, config.max_length_ratio)
        if issue:
            issues.append(issue)

        if config.validate_html_integrity:
            issue = check_html_integrity(segment, record)
            if issue:
                issues.append(issue)

        issue = check_missing_urls(segment, record)
        if issue:
            issues.append(issue)

    total = len(pairs)
    warnings = sum(1 for i in issues if i.severity == "warning")
    errors = sum(1 for i in issues if i.severity == "error")

    return ValidationReport(
        total_checked=total,
        issues=issues,
        passed=total - len({i.segment_id for i in issues}),
        warnings=warnings,
        errors=errors,
    )


def save_validation_report(report: ValidationReport, output_dir: Path) -> None:
    data = {
        "total_checked": report.total_checked,
        "passed": report.passed,
        "warnings": report.warnings,
        "errors": report.errors,
        "issues": [
            {
                "segment_id": i.segment_id,
                "check": i.check,
                "severity": i.severity,
                "detail": i.detail,
            }
            for i in report.issues
        ],
    }
    (output_dir / "validation_report.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
