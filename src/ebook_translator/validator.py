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
    # Stop at whitespace, quotes, and angle brackets so URLs embedded in HTML
    # attributes (href="...") are captured without trailing markup. Also trim
    # common trailing punctuation.
    urls = re.findall(r"""https?://[^\s"'<>]+""", text)
    trailing = ".,;:!?)]}。，、；：！？）"
    return {u.rstrip(trailing) for u in urls}


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


_ASCII_LETTER_RE = re.compile(r"[A-Za-z]")


def _ascii_letter_ratio(text: str) -> float:
    """Fraction of characters in *text* that are ASCII letters (A-Z / a-z)."""
    total = len(text)
    if total == 0:
        return 0.0
    return len(_ASCII_LETTER_RE.findall(text)) / total


def detect_untranslated_text(
    segment: Segment,
    record: TranslationRecord,
    ascii_threshold: float = 0.75,
) -> ValidationIssue | None:
    """Flag a translation that is still predominantly in the source language.

    Heuristic: if the translation contains a very high proportion of ASCII
    letters (> ``ascii_threshold``) AND the source text is also ASCII-heavy
    (so we don't falsely flag translations of code/URLs/numbers), the model
    most likely echoed or produced the original English text verbatim.

    The check is intentionally lenient (default 0.75) to avoid false positives
    on bilingual segments that mix CJK with some English proper nouns.
    """
    trg = record.translation.strip()
    if not trg:
        return None  # empty_translation check handles this
    src = segment.source_text.strip()
    if not src:
        return None

    # Only apply when source is also ASCII-heavy (i.e. an English source).
    src_ratio = _ascii_letter_ratio(src)
    if src_ratio < 0.5:
        return None  # Source is not ASCII-dominant; skip.

    trg_ratio = _ascii_letter_ratio(trg)
    if trg_ratio >= ascii_threshold:
        return ValidationIssue(
            segment_id=segment.segment_id,
            check="untranslated_text",
            severity="error",
            detail=(
                f"Translation appears to be in the source language "
                f"(ASCII-letter ratio {trg_ratio:.0%} ≥ threshold {ascii_threshold:.0%})."
            ),
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


# Common Simplified-only characters (their Traditional form differs). Presence of
# any of these in the output indicates the model produced Simplified Chinese.
SIMPLIFIED_CHARS = set(
    "开关门们这国对来时学实发会样难应进过还说现觉觉际"
    "东车书长马鸟鱼语职术语丽义乐买卖钱银铁银电脑网络"
    "经济临团队员问题间办点热爱护尽红绿见观让认让动单"
    "为产业丰专与丛个临举乱争亏亚亲仅从仑仓仪们价众优"
    "传伤伦体余佣侠侣俭俩储儿党兰关兴兵养兽内冈册写军"
    "农冯冲决况减凉凑击凤凭凯击刘则刚创删别剥剧劝办务"
    "动励劳势勋勤区医华协单卖卢卫却厂厅历厉压厌厍县参"
)

# Leading bracketed prefix such as 【第一章：…】, ［…］, [...], （…）
_PREFIX_RE = re.compile(r"^\s*[【\[［（(][^】\]］）)]*[】\]］）)]")

# Markdown code fence anywhere in the output.
_FENCE_RE = re.compile(r"```")

# Explanatory prefixes the model sometimes prepends.
_EXPLANATION_RE = re.compile(
    r"^\s*(翻譯如下|翻译如下|譯文如下|译文如下|譯文[:：]|译文[:：]|"
    r"以下是(本文|該|这|這)?的?(翻譯|翻译|譯文|译文)|"
    r"here\s+is\s+the\s+translation|translation\s*[:：])",
    re.IGNORECASE,
)


def detect_simplified_chinese(segment: Segment, record: TranslationRecord) -> ValidationIssue | None:
    found = sorted({ch for ch in record.translation if ch in SIMPLIFIED_CHARS})
    if found:
        return ValidationIssue(
            segment_id=segment.segment_id,
            check="simplified_chinese",
            severity="error",
            detail=f"Translation contains Simplified Chinese characters: {''.join(found)}",
        )
    return None


def detect_added_prefix(segment: Segment, record: TranslationRecord) -> ValidationIssue | None:
    m = _PREFIX_RE.match(record.translation)
    if m:
        return ValidationIssue(
            segment_id=segment.segment_id,
            check="added_prefix",
            severity="error",
            detail=f"Translation begins with an added bracketed prefix: {m.group(0).strip()!r}",
        )
    return None


def detect_markdown_fence(segment: Segment, record: TranslationRecord) -> ValidationIssue | None:
    if _FENCE_RE.search(record.translation):
        return ValidationIssue(
            segment_id=segment.segment_id,
            check="markdown_fence",
            severity="error",
            detail="Translation contains a Markdown code fence (```).",
        )
    return None


def detect_explanation_prefix(segment: Segment, record: TranslationRecord) -> ValidationIssue | None:
    m = _EXPLANATION_RE.match(record.translation)
    if m:
        return ValidationIssue(
            segment_id=segment.segment_id,
            check="explanation_prefix",
            severity="error",
            detail=f"Translation begins with an explanatory prefix: {m.group(0).strip()!r}",
        )
    return None


def classify_failure(error: str | None) -> str:
    """Map a failed record's error message to a coarse failure category."""
    if not error:
        return "unknown"
    low = error.lower()
    if "empty message content" in low or "nonetype" in low and "strip" in low:
        return "empty message content"
    if "timed out" in low or "timeout" in low:
        return "timeout"
    if "context length" in low:
        return "context exceeded"
    if "authentication failed" in low:
        return "fatal provider error"
    if "endpoint not found" in low or "(404)" in low:
        return "fatal provider error"
    if "rate limit" in low:
        return "rate limit"
    if "provider error (5" in low:
        return "provider 5xx"
    if "expecting value" in low or "json" in low or "keyerror" in low or "malformed" in low:
        return "malformed response"
    return "fatal provider error"


def check_record_status(segment: Segment, record: TranslationRecord) -> ValidationIssue | None:
    if record.status == "completed":
        return None
    if record.status == "quality_failed":
        detail = "Translation failed quality gate."
        if record.error:
            detail = f"{detail} Checks: {record.error}"
        return ValidationIssue(
            segment_id=segment.segment_id,
            check="quality_failed",
            severity="error",
            detail=detail,
        )
    detail = "Translation did not complete."
    if record.error:
        detail = f"{detail} Error: {record.error}"
    return ValidationIssue(
        segment_id=segment.segment_id,
        check="translation_failed",
        severity="error",
        detail=detail,
    )


def check_quality(
    segment: Segment, translation: str, config: QualityConfig
) -> list[ValidationIssue]:
    """Run the translation-quality checks on a candidate translation string.

    Used both by the live quality gate (before marking a record completed) and by
    quality-failed detection over stored records.
    """
    from datetime import datetime, timezone

    rec = TranslationRecord(
        segment_id=segment.segment_id,
        source_hash=segment.sha1_prefix,
        status="completed",
        source=segment.source_html,
        translation=translation,
        model="",
        attempt=0,
        created_at=datetime.now(timezone.utc),
    )
    issues: list[ValidationIssue] = []
    if config.validate_simplified_chinese:
        if (i := detect_simplified_chinese(segment, rec)):
            issues.append(i)
    if config.validate_added_prefix:
        if (i := detect_added_prefix(segment, rec)):
            issues.append(i)
    if config.validate_markdown_fence:
        if (i := detect_markdown_fence(segment, rec)):
            issues.append(i)
    if config.validate_explanation_prefix:
        if (i := detect_explanation_prefix(segment, rec)):
            issues.append(i)
    if config.validate_untranslated_text:
        if (i := detect_untranslated_text(segment, rec, config.untranslated_ascii_threshold)):
            issues.append(i)
    return issues


def validate_translations(
    pairs: list[tuple[Segment, TranslationRecord]],
    config: QualityConfig,
) -> ValidationReport:
    issues: list[ValidationIssue] = []

    # Keep only the latest record per segment_id, so a later successful retry
    # supersedes an earlier failed record (and is not double-counted).
    deduped: dict[str, tuple[Segment, TranslationRecord]] = {}
    for segment, record in pairs:
        deduped[segment.segment_id] = (segment, record)
    pairs = list(deduped.values())

    for segment, record in pairs:
        if record.status != "completed":
            issue = check_record_status(segment, record)
            if issue:
                issues.append(issue)
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

        if config.validate_simplified_chinese:
            issue = detect_simplified_chinese(segment, record)
            if issue:
                issues.append(issue)

        if config.validate_added_prefix:
            issue = detect_added_prefix(segment, record)
            if issue:
                issues.append(issue)

        if config.validate_markdown_fence:
            issue = detect_markdown_fence(segment, record)
            if issue:
                issues.append(issue)

        if config.validate_explanation_prefix:
            issue = detect_explanation_prefix(segment, record)
            if issue:
                issues.append(issue)

        if config.validate_untranslated_text:
            issue = detect_untranslated_text(segment, record, config.untranslated_ascii_threshold)
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


# Checks that indicate a translation completed but is unacceptable quality and
# therefore eligible for a quality-failed retry (as opposed to a hard failure).
QUALITY_CHECKS = frozenset(
    {"simplified_chinese", "added_prefix", "markdown_fence", "explanation_prefix", "untranslated_text"}
)


def quality_failed_segment_ids(
    pairs: list[tuple[Segment, TranslationRecord]],
    config: QualityConfig,
) -> set[str]:
    """Return segment_ids whose latest record is quality-failed.

    Considers only the most recent record per segment. A segment is quality-failed
    when its latest record either has status ``quality_failed`` (set by the live
    quality gate) or is ``completed`` but still trips a quality check (legacy
    records created before the gate existed).
    """
    latest: dict[str, tuple[Segment, TranslationRecord]] = {}
    for segment, record in pairs:
        latest[segment.segment_id] = (segment, record)

    result: set[str] = set()
    for sid, (segment, record) in latest.items():
        if record.status == "quality_failed":
            result.add(sid)
        elif record.status == "completed" and check_quality(segment, record.translation, config):
            result.add(sid)
    return result


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
