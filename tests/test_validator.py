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
    check_record_status,
    classify_failure,
    detect_added_prefix,
    detect_explanation_prefix,
    detect_markdown_fence,
    detect_simplified_chinese,
    quality_failed_segment_ids,
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


def test_check_missing_urls_passes_for_html_anchor():
    # URL embedded in an <a href> must not trip the check when preserved.
    seg = make_segment(
        source_html='For more details, visit <a href="https://example.com/intro">the introduction</a>.'
    )
    rec = make_record('如需更多詳細資訊，請造訪<a href="https://example.com/intro">緒論</a>。')
    assert check_missing_urls(seg, rec) is None


def test_check_record_status_detects_failed_translation():
    issue = check_record_status(make_segment(), make_record("", status="failed"))
    assert issue is not None
    assert issue.check == "translation_failed"
    assert issue.severity == "error"


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


def test_validate_reports_failed_records():
    cfg = QualityConfig()
    seg = make_segment()
    rec = make_record("", status="failed")  # failed, not completed
    report = validate_translations([(seg, rec)], cfg)
    assert report.errors == 1
    assert report.issues[0].check == "translation_failed"


# ---------------------------------------------------------------------------
# Phase 2.5 translation-quality validators
# ---------------------------------------------------------------------------

def test_detect_simplified_chinese_flags_simplified():
    seg = make_segment()
    rec = make_record("这是开端")  # 这/开 are Simplified
    issue = detect_simplified_chinese(seg, rec)
    assert issue is not None
    assert issue.check == "simplified_chinese"
    assert issue.severity == "error"


def test_detect_simplified_chinese_returns_matches():
    seg = make_segment()
    rec = make_record("美国军队")  # 国 is Simplified
    issue = detect_simplified_chinese(seg, rec)
    assert issue is not None
    assert issue.matches, "matches should not be empty"
    texts = [m["text"] for m in issue.matches]
    assert "国" in texts
    # Position should be correct (国 is at index 1)
    pos_map = {m["text"]: m["position"] for m in issue.matches}
    assert pos_map["国"] == 1
    # Suggestion should be Traditional form
    suggestions = {m["text"]: m["suggestion"] for m in issue.matches}
    assert suggestions["国"] == "國"


def test_detect_simplified_chinese_traditional_not_flagged():
    seg = make_segment()
    rec = make_record("美國軍隊")  # 國 is Traditional — should not trigger
    assert detect_simplified_chinese(seg, rec) is None


def test_detect_simplified_chinese_passes_traditional():
    seg = make_segment()
    rec = make_record("這是開端，那是一個明亮的寒冷四月天。")
    assert detect_simplified_chinese(seg, rec) is None


def test_detect_simplified_chinese_matches_include_all_occurrences():
    seg = make_segment()
    rec = make_record("这国这")  # 这 appears twice, 国 once
    issue = detect_simplified_chinese(seg, rec)
    assert issue is not None
    assert len(issue.matches) == 3  # two 这 + one 国


# False-positive regression tests: chars identical in Simplified and Traditional
# must NOT trigger the simplified_chinese check.

def test_detect_simplified_chinese_qin_not_flagged():
    """勤 is the same codepoint in both scripts — must not be flagged."""
    seg = make_segment()
    rec = make_record("外勤辦公室")  # 勤 should not trigger
    assert detect_simplified_chinese(seg, rec) is None


def test_detect_simplified_chinese_bing_not_flagged():
    """兵 is the same codepoint in both scripts — must not be flagged."""
    seg = make_segment()
    rec = make_record("憲兵部隊")  # 兵 should not trigger
    assert detect_simplified_chinese(seg, rec) is None


def test_detect_simplified_chinese_meiguo_traditional_not_flagged():
    """美國軍隊 uses all Traditional characters — must not be flagged."""
    seg = make_segment()
    rec = make_record("美國軍隊")
    assert detect_simplified_chinese(seg, rec) is None


def test_detect_simplified_chinese_meiguo_simplified_flagged():
    """美国軍隊 contains Simplified 国 — must be flagged with correct match."""
    seg = make_segment()
    rec = make_record("美国軍隊")
    issue = detect_simplified_chinese(seg, rec)
    assert issue is not None
    assert issue.check == "simplified_chinese"
    texts = [m["text"] for m in issue.matches]
    assert "国" in texts
    sugs = {m["text"]: m["suggestion"] for m in issue.matches}
    assert sugs["国"] == "國"


def test_detect_simplified_chinese_full_simplified_flagged():
    """这是一个测试 — all Simplified, must be flagged."""
    seg = make_segment()
    rec = make_record("这是一个测试")
    assert detect_simplified_chinese(seg, rec) is not None


def test_detect_simplified_chinese_full_traditional_not_flagged():
    """這是一個測試 — all Traditional, must not be flagged."""
    seg = make_segment()
    rec = make_record("這是一個測試")
    assert detect_simplified_chinese(seg, rec) is None


def test_save_validation_report_includes_matches(tmp_path):
    from ebook_translator.validator import ValidationReport, ValidationIssue, save_validation_report
    issue = ValidationIssue(
        segment_id="0:0:aa",
        check="simplified_chinese",
        severity="error",
        detail="contains simplified",
        matches=[{"text": "国", "position": 1, "suggestion": "國"}],
    )
    report = ValidationReport(total_checked=1, issues=[issue], passed=0, warnings=0, errors=1)
    save_validation_report(report, tmp_path)
    import json
    data = json.loads((tmp_path / "validation_report.json").read_text(encoding="utf-8"))
    saved_issue = data["issues"][0]
    assert "matches" in saved_issue
    assert saved_issue["matches"][0]["text"] == "国"
    assert saved_issue["matches"][0]["suggestion"] == "國"


def test_save_validation_report_no_matches_omitted(tmp_path):
    from ebook_translator.validator import ValidationReport, ValidationIssue, save_validation_report
    issue = ValidationIssue(
        segment_id="0:0:aa",
        check="empty_translation",
        severity="error",
        detail="empty",
    )
    report = ValidationReport(total_checked=1, issues=[issue], passed=0, warnings=0, errors=1)
    save_validation_report(report, tmp_path)
    import json
    data = json.loads((tmp_path / "validation_report.json").read_text(encoding="utf-8"))
    saved_issue = data["issues"][0]
    assert "matches" not in saved_issue


def test_detect_added_prefix_flags_chapter_prefix():
    seg = make_segment()
    rec = make_record("【第一章：開端】那是一個明亮的寒冷四月天。")
    issue = detect_added_prefix(seg, rec)
    assert issue is not None
    assert issue.check == "added_prefix"


def test_detect_added_prefix_passes_clean():
    seg = make_segment()
    rec = make_record("那是一個明亮的寒冷四月天。")
    assert detect_added_prefix(seg, rec) is None


def test_detect_markdown_fence_flags_fence():
    seg = make_segment()
    rec = make_record("```\n那是一個明亮的寒冷四月天。\n```")
    issue = detect_markdown_fence(seg, rec)
    assert issue is not None
    assert issue.check == "markdown_fence"


def test_detect_explanation_prefix_flags_note():
    seg = make_segment()
    rec = make_record("翻譯如下：那是一個明亮的寒冷四月天。")
    issue = detect_explanation_prefix(seg, rec)
    assert issue is not None
    assert issue.check == "explanation_prefix"


def test_detect_explanation_prefix_passes_clean():
    seg = make_segment()
    rec = make_record("那是一個明亮的寒冷四月天。")
    assert detect_explanation_prefix(seg, rec) is None


def test_phase2_bad_translation_flags_prefix_and_simplified():
    """The exact Phase 2 bad output should now be caught."""
    cfg = QualityConfig()
    seg = make_segment(source_html="It was a <em>bright</em> cold day.")
    rec = make_record("【第一章：开端】那是一個<em>明亮</em>的寒冷四月天。")
    report = validate_translations([(seg, rec)], cfg)
    checks = {i.check for i in report.issues}
    assert "added_prefix" in checks
    assert "simplified_chinese" in checks


def test_validate_latest_success_supersedes_failed():
    """A later completed record for the same segment must clear the earlier failure."""
    cfg = QualityConfig()
    seg = make_segment()
    failed = make_record("", status="failed")
    completed = make_record("那是一個明亮的寒冷四月天。", status="completed")
    # Same segment_id, failed first then completed
    report = validate_translations([(seg, failed), (seg, completed)], cfg)
    assert report.errors == 0
    assert report.total_checked == 1


def test_classify_failure_empty_content():
    assert classify_failure("Provider returned empty message content") == "empty message content"
    assert classify_failure("'NoneType' object has no attribute 'strip'") == "empty message content"


def test_classify_failure_categories():
    assert classify_failure("Request timed out: ...") == "timeout"
    assert classify_failure("Context length exceeded: ...") == "context exceeded"
    assert classify_failure("Rate limit exceeded: ...") == "rate limit"
    assert classify_failure("Provider error (500): boom") == "provider 5xx"
    assert classify_failure("Provider endpoint not found (404): nope") == "fatal provider error"
    assert classify_failure(None) == "unknown"


def test_quality_failed_segment_ids_detects_quality_issues():
    cfg = QualityConfig()
    seg = make_segment()
    bad = make_record("【第一章：开端】这是简体譯文")  # added_prefix + simplified
    assert quality_failed_segment_ids([(seg, bad)], cfg) == {seg.segment_id}


def test_quality_failed_segment_ids_ignores_clean():
    cfg = QualityConfig()
    seg = make_segment()
    clean = make_record("這是乾淨的繁體譯文。")
    assert quality_failed_segment_ids([(seg, clean)], cfg) == set()


def test_quality_failed_latest_clean_record_supersedes():
    cfg = QualityConfig()
    seg = make_segment()
    bad = make_record("【第一章：开端】这是简体譯文")
    clean = make_record("這是乾淨的繁體譯文。")
    # Old bad record then a newer clean record for the same segment_id
    assert quality_failed_segment_ids([(seg, bad), (seg, clean)], cfg) == set()


def test_quality_failed_does_not_include_hard_failed():
    cfg = QualityConfig()
    seg = make_segment()
    failed = make_record("", status="failed")  # hard failure, not a quality issue
    assert quality_failed_segment_ids([(seg, failed)], cfg) == set()


# ---------------------------------------------------------------------------
# Phase 6: detect_untranslated_text
# ---------------------------------------------------------------------------

def test_detect_untranslated_text_flags_english_echo():
    """If the translation is still English (high ASCII ratio), it should be flagged."""
    from ebook_translator.validator import detect_untranslated_text
    seg = make_segment("The quick brown fox jumps over the lazy dog.")
    rec = make_record("The quick brown fox jumps over the lazy dog.")  # echoed source
    issue = detect_untranslated_text(seg, rec)
    assert issue is not None
    assert issue.check == "untranslated_text"
    assert issue.severity == "error"


def test_detect_untranslated_text_passes_chinese_translation():
    """A proper Chinese translation must not be flagged."""
    from ebook_translator.validator import detect_untranslated_text
    seg = make_segment("The quick brown fox jumps over the lazy dog.")
    rec = make_record("那隻敏捷的棕色狐狸跳過了懶惰的狗。")
    assert detect_untranslated_text(seg, rec) is None


def test_detect_untranslated_text_ignores_cjk_source():
    """If the source is already CJK-dominant, skip the check (no false positives)."""
    from ebook_translator.validator import detect_untranslated_text
    seg = make_segment("這是一段中文文字。")
    rec = make_record("This would look suspicious but source is CJK so skip.")
    assert detect_untranslated_text(seg, rec) is None


def test_detect_untranslated_text_partial_english_below_threshold():
    """A translation that mixes CJK with some English proper nouns (< threshold) passes."""
    from ebook_translator.validator import detect_untranslated_text
    seg = make_segment("Winston Smith walked into the room.")
    # ~50% ASCII letters — below default 0.75 threshold
    rec = make_record("溫斯頓·史密斯 walked into 房間。")
    assert detect_untranslated_text(seg, rec) is None


def test_quality_gate_completed_english_echo_marked_quality_failed():
    """quality_failed_segment_ids must catch a completed-but-untranslated segment."""
    cfg = QualityConfig()
    seg = make_segment("The quick brown fox jumps over the lazy dog.")
    rec = make_record("The quick brown fox jumps over the lazy dog.", status="completed")
    # Should be flagged as quality_failed (untranslated_text check)
    result = quality_failed_segment_ids([(seg, rec)], cfg)
    assert seg.segment_id in result


def test_validate_translations_flags_english_echo_as_error():
    """validate_translations must surface the untranslated_text issue as an error."""
    cfg = QualityConfig()
    seg = make_segment("The quick brown fox jumps over the lazy dog.")
    rec = make_record("The quick brown fox jumps over the lazy dog.", status="completed")
    report = validate_translations([(seg, rec)], cfg)
    checks = {i.check for i in report.issues}
    assert "untranslated_text" in checks
    assert any(i.severity == "error" for i in report.issues if i.check == "untranslated_text")


# ---------------------------------------------------------------------------
# Phase 6: missing_translation_report
# ---------------------------------------------------------------------------

def _make_spine_doc(html: str, href: str = "chap01.xhtml") :
    from ebook_translator.epub.reader import SpineDocument
    return SpineDocument(
        chapter_index=0,
        item_id="item1",
        href=href,
        content=html.encode("utf-8"),
        media_type="application/xhtml+xml",
    )


def test_missing_report_finds_failed_block(tmp_path):
    """build_missing_translation_report must include a failed translation."""
    from datetime import datetime, timezone
    from ebook_translator.missing_report import build_missing_translation_report, REASON_FAILED
    from ebook_translator.models import TranslationRecord
    from ebook_translator.segmenter.segmenter import segment_document

    html = "<html><body><p>Hello world.</p></body></html>"
    doc = _make_spine_doc(html)
    segs = segment_document(doc)
    assert len(segs) == 1
    seg = segs[0]

    rec = TranslationRecord(
        segment_id=seg.segment_id,
        source_hash=seg.sha1_prefix,
        status="failed",
        source=seg.source_html,
        translation="",
        model="mock",
        attempt=1,
        error="Provider returned empty message content",
        created_at=datetime.now(timezone.utc),
    )
    translations = {seg.segment_id: rec}

    report = build_missing_translation_report([doc], segs, translations)
    assert len(report) == 1
    entry = report[0]
    assert entry["reason"] == REASON_FAILED
    assert entry["has_segment"] is True
    assert entry["segment_id"] == seg.segment_id
    assert "Hello world" in entry["source_text"]


def test_missing_report_finds_no_segment_block(tmp_path):
    """build_missing_translation_report must report blocks that had no segment at all."""
    from ebook_translator.missing_report import build_missing_translation_report, REASON_NO_SEGMENT

    html = "<html><body><p>Unextracted paragraph.</p></body></html>"
    doc = _make_spine_doc(html)
    # Pass empty segment list — simulates a segmenter miss
    report = build_missing_translation_report([doc], [], {})
    assert len(report) == 1
    entry = report[0]
    assert entry["reason"] == REASON_NO_SEGMENT
    assert entry["has_segment"] is False
    assert entry["segment_id"] is None


def test_missing_report_excludes_completed_blocks(tmp_path):
    """Blocks with a completed translation must NOT appear in the report."""
    from datetime import datetime, timezone
    from ebook_translator.missing_report import build_missing_translation_report
    from ebook_translator.models import TranslationRecord
    from ebook_translator.segmenter.segmenter import segment_document

    html = "<html><body><p>Successfully translated.</p></body></html>"
    doc = _make_spine_doc(html)
    segs = segment_document(doc)
    seg = segs[0]

    rec = TranslationRecord(
        segment_id=seg.segment_id,
        source_hash=seg.sha1_prefix,
        status="completed",
        source=seg.source_html,
        translation="成功翻譯。",
        model="mock",
        attempt=1,
        created_at=datetime.now(timezone.utc),
    )
    report = build_missing_translation_report([doc], segs, {seg.segment_id: rec})
    assert report == [], f"Expected empty report, got: {report}"

