from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from ebook_translator.checkpoint import CheckpointManager
from ebook_translator.providers.base import TranslationResponse
from ebook_translator.translator import run_translation
from tests.conftest import MockTranslationProvider, make_sample_config


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
async def test_quality_gate_simplified_marks_quality_failed(tmp_path, sample_epub_path):
    cfg = make_sample_config(tmp_path, sample_epub_path)
    prov = _FixedProvider("这是简体译文")  # Simplified Chinese
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=prov):
        await run_translation(cfg)

    ck = CheckpointManager(_book_dir(cfg))
    records = ck.load_all_translations()
    assert records  # something was written
    assert all(r.status == "quality_failed" for r in records.values())


@pytest.mark.asyncio
async def test_quality_gate_prefix_marks_quality_failed(tmp_path, sample_epub_path):
    cfg = make_sample_config(tmp_path, sample_epub_path)
    prov = _FixedProvider("【第一章：開端】這是內容。")  # added chapter prefix
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=prov):
        await run_translation(cfg)

    ck = CheckpointManager(_book_dir(cfg))
    records = ck.load_all_translations()
    assert all(r.status == "quality_failed" for r in records.values())
    # error field records which checks tripped
    assert any("added_prefix" in (r.error or "") for r in records.values())


@pytest.mark.asyncio
async def test_quality_failed_not_counted_as_completed(tmp_path, sample_epub_path):
    cfg = make_sample_config(tmp_path, sample_epub_path)
    prov = _FixedProvider("这是简体译文")
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=prov):
        await run_translation(cfg)

    ck = CheckpointManager(_book_dir(cfg))
    assert ck.load_completed_ids() == set()       # none completed
    assert ck.load_failed_ids() == {}             # quality_failed is not a hard failure
    state = ck.load_state()
    assert state.status != "completed"


@pytest.mark.asyncio
async def test_retry_quality_failed_recovers_quality_failed_status(tmp_path, sample_epub_path):
    cfg = make_sample_config(tmp_path, sample_epub_path)

    # First run: provider returns Simplified -> all quality_failed
    bad = _FixedProvider("这是简体译文")
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=bad):
        await run_translation(cfg)

    ck = CheckpointManager(_book_dir(cfg))
    assert len(ck.load_completed_ids()) == 0
    total = len(ck.load_segments())
    assert total > 0

    # retry-quality-failed with a clean provider -> all recovered to completed
    clean = MockTranslationProvider()
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=clean):
        await run_translation(cfg, quality_failed_only=True)

    assert len(ck.load_completed_ids()) == total
    assert clean.call_count == total
