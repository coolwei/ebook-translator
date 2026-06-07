from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from ebook_translator.checkpoint import CheckpointManager
from ebook_translator.config import QualityConfig
from ebook_translator.fallback import should_fallback_from_quality
from ebook_translator.models import Segment, TranslationRecord
from ebook_translator.missing_report import build_missing_translation_report
from ebook_translator.providers.base import TranslationResponse
from ebook_translator.translator import run_translation
from ebook_translator.validator import evaluate_quality_gate, validate_translations
from tests.conftest import MockTranslationProvider, make_sample_config


def _seg() -> Segment:
    return Segment(
        segment_id="0000:0000:aabb",
        chapter_index=0,
        block_index=0,
        sha1_prefix="aabb",
        source_text="US Army",
        source_html="US Army",
        tag_name="p",
        chapter_href="chap01.xhtml",
    )


def test_meiguo_jundui_warning_when_relaxed():
    cfg = QualityConfig(strict_mode=False)
    gate = evaluate_quality_gate(_seg(), "美国軍隊", cfg)
    assert not gate.has_errors
    assert gate.has_warnings
    assert gate.warnings[0].check == "simplified_chinese"
    assert gate.warnings[0].matches


def test_meiguo_jundui_error_when_strict():
    cfg = QualityConfig(strict_mode=True)
    gate = evaluate_quality_gate(_seg(), "美国軍隊", cfg)
    assert gate.has_errors
    assert any(i.check == "simplified_chinese" for i in gate.errors)


def test_meiguo_traditional_no_simplified_issue():
    cfg = QualityConfig(strict_mode=False)
    gate = evaluate_quality_gate(_seg(), "美國軍隊", cfg)
    assert not gate.has_errors
    assert not gate.has_warnings


def test_many_simplified_chars_still_hard_fail():
    cfg = QualityConfig(strict_mode=False)
    gate = evaluate_quality_gate(_seg(), "这是这是一个国国国测试内容很多简体", cfg)
    assert gate.has_errors
    assert any(i.check == "simplified_chinese" for i in gate.errors)


def test_markdown_fence_still_hard_fail():
    cfg = QualityConfig(strict_mode=False)
    gate = evaluate_quality_gate(_seg(), "```\n美國軍隊\n```", cfg)
    assert gate.has_errors
    assert any(i.check == "markdown_fence" for i in gate.errors)


def test_untranslated_text_still_hard_fail():
    cfg = QualityConfig(strict_mode=False)
    seg = Segment(
        segment_id="0000:0001:bbcc",
        chapter_index=0,
        block_index=1,
        sha1_prefix="bbcc",
        source_text="The quick brown fox jumps over the lazy dog.",
        source_html="The quick brown fox jumps over the lazy dog.",
        tag_name="p",
        chapter_href="chap01.xhtml",
    )
    gate = evaluate_quality_gate(
        seg, "The quick brown fox jumps over the lazy dog.", cfg
    )
    assert gate.has_errors
    assert any(i.check == "untranslated_text" for i in gate.errors)


def test_warning_only_does_not_trigger_fallback():
    assert not should_fallback_from_quality("simplified_chinese", strict_mode=False)
    assert not should_fallback_from_quality("added_prefix", strict_mode=False)
    assert should_fallback_from_quality("markdown_fence", strict_mode=False)


class _FixedProvider(MockTranslationProvider):
    def __init__(self, text: str):
        super().__init__()
        self._text = text

    async def translate(self, request):
        self.call_count += 1
        return TranslationResponse(
            translated_text=self._text,
            model=request.model,
            finish_reason="stop",
            prompt_tokens=1,
            completion_tokens=1,
        )


def _book_dir(cfg):
    return next(cfg.project.output_dir.iterdir())


@pytest.mark.asyncio
async def test_relaxed_simplified_completes_with_warnings(tmp_path, sample_epub_path):
    cfg = make_sample_config(tmp_path, sample_epub_path)
    cfg.quality = QualityConfig(strict_mode=False)
    prov = _FixedProvider("美国軍隊")
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=prov):
        await run_translation(cfg)

    ck = CheckpointManager(_book_dir(cfg))
    records = ck.load_all_translations()
    assert all(r.status == "completed" for r in records.values())
    assert all(r.quality_warnings and "simplified_chinese" in r.quality_warnings for r in records.values())
    assert ck.load_completed_ids() == set(records)


@pytest.mark.asyncio
async def test_warning_completed_not_missing(tmp_path, sample_epub_path):
    from ebook_translator.epub.reader import read_epub

    cfg = make_sample_config(tmp_path, sample_epub_path)
    cfg.quality = QualityConfig(strict_mode=False)
    prov = _FixedProvider("美国軍隊")
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=prov):
        await run_translation(cfg)

    book_dir = _book_dir(cfg)
    ck = CheckpointManager(book_dir)
    _, spine = read_epub(cfg.input.path)
    report = build_missing_translation_report(
        spine, ck.load_segments(), ck.load_all_translations()
    )
    assert report == []


def test_validate_splits_warnings_and_errors():
    cfg = QualityConfig(strict_mode=False)
    seg = _seg()
    rec = TranslationRecord(
        segment_id=seg.segment_id,
        source_hash=seg.sha1_prefix,
        status="completed",
        source=seg.source_html,
        translation="美国軍隊",
        model="m",
        attempt=1,
        created_at=datetime.now(timezone.utc),
    )
    report = validate_translations([(seg, rec)], cfg)
    assert report.errors == 0
    assert report.warnings >= 1
    assert any(i.check == "simplified_chinese" and i.severity == "warning" for i in report.issues)