from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ebook_translator.config import QualityConfig
from ebook_translator.models import Segment, TranslationRecord
from ebook_translator.validator import (
    check_empty,
    check_html_integrity,
    check_identical,
    check_length_ratio,
    check_missing_urls,
    validate_translations,
)


def make_segment(source_text: str = "Hello world.", source_html: str | None = None) -> Segment:
    return Segment(
        segment_id="0000:0000:aabb",
        chapter_index=0,
        block_index=0,
        sha1_prefix="aabb",
        source_text=source_text,
        source_html=source_html or source_text,
        tag_name="p",
        chapter_href="chap01.xhtml",
    )


def make_record(translation: str, status: str = "completed") -> TranslationRecord:
    return TranslationRecord(
        segment_id="0000:0000:aabb",
        source_hash="aabb",
        status=status,
        source="Hello world.",
        translation=translation,
        model="test-model",
        attempt=1,
        created_at=datetime.now(timezone.utc),
    )


def test_check_empty_detects_empty_translation():
    issue = check_empty(make_segment(), make_record(""))
    assert issue is not None
    assert issue.severity == "error"
    assert issue.check == "empty_translation"


def test_check_empty_passes_nonempty():
    assert check_empty(make_segment(), make_record("你好世界。")) is None


def test_check_identical_detects_same_text():
    seg = make_segment("hello world")
    rec = make_record("hello world")
    issue = check_identical(seg, rec)
    assert issue is not None
    assert issue.check == "identical_translation"


def test_check_identical_passes_different_text():
    assert check_identical(make_segment("Hello"), make_record("你好")) is None


def test_check_length_ratio_detects_too_long():
    seg = make_segment("Hi")
    # Translation 10x longer
    rec = make_record("x" * 20)
    issue = check_length_ratio(seg, rec, max_ratio=3.0)
    assert issue is not None
    assert issue.check == "length_ratio"


def test_check_length_ratio_passes_within_bounds():
    seg = make_segment("Hello world this is a test sentence.")
    rec = make_record("你好世界這是一個測試句子。")
    assert check_length_ratio(seg, rec, max_ratio=3.0) is None


def test_check_html_integrity_detects_tag_mismatch():
    seg = make_segment(source_html='<em>Hello</em> world')
    rec = make_record("你好世界")  # missing <em> tag
    issue = check_html_integrity(seg, rec)
    assert issue is not None
    assert issue.check == "html_integrity"


def test_check_html_integrity_passes_matching_tags():
    seg = make_segment(source_html='<em>Hello</em> world')
    rec = make_record("<em>你好</em> 世界")
    assert check_html_integrity(seg, rec) is None


def test_check_missing_urls_detects_missing():
    seg = make_segment(source_html='See <a href="https://example.com">here</a>')
    rec = make_record("參見這裡")  # URL missing
    issue = check_missing_urls(seg, rec)
    assert issue is not None
    assert "example.com" in issue.detail


def test_check_missing_urls_passes_when_present():
    seg = make_segment(source_html='See https://example.com for details')
    rec = make_record("參見 https://example.com 了解詳情")
    assert check_missing_urls(seg, rec) is None


def test_validate_translations_aggregates():
    cfg = QualityConfig()
    seg = make_segment()
    rec_ok = make_record("你好世界。")
    rec_empty = make_record("")

    pairs = [
        (seg, rec_ok),
        (Segment(
            segment_id="0000:0001:ccdd",
            chapter_index=0,
            block_index=1,
            sha1_prefix="ccdd",
            source_text="Test",
            source_html="Test",
            tag_name="p",
            chapter_href="chap01.xhtml",
        ), rec_empty),
    ]
    report = validate_translations(pairs, cfg)
    assert report.total_checked == 2
    assert report.errors >= 1


def test_validate_skips_failed_records():
    cfg = QualityConfig()
    seg = make_segment()
    rec = make_record("", status="failed")  # failed, not completed
    report = validate_translations([(seg, rec)], cfg)
    # Failed records are skipped; no issues raised
    assert report.errors == 0
