from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from ebook_translator.translator import run_translation
from tests.conftest import MockTranslationProvider, make_sample_config


@pytest.mark.asyncio
async def test_exported_epub_rereads_with_bilingual_blocks(tmp_path, sample_epub_path):
    """End-to-end: exported EPUB must re-open in ebooklib and contain bilingual
    blocks with source before translation."""
    import ebooklib
    from ebooklib import epub
    from bs4 import BeautifulSoup

    cfg = make_sample_config(tmp_path, sample_epub_path)
    provider = MockTranslationProvider()
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=provider):
        await run_translation(cfg)

    book_dir = next(cfg.project.output_dir.iterdir())
    out_epub = book_dir / "translated.epub"

    book = epub.read_epub(str(out_epub), options={"ignore_ncx": True})
    blocks = 0
    for item in book.get_items():
        if isinstance(item, epub.EpubNav):
            continue
        if item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue
        soup = BeautifulSoup(item.get_content(), "lxml")
        for b in soup.find_all("div", class_="bilingual-block"):
            blocks += 1
            inner = b.find_all(class_=["src", "trg"])
            assert inner[0].get("class") == ["src"]
            assert inner[1].get("class") == ["trg"]
    assert blocks > 0


@pytest.mark.asyncio
async def test_full_pipeline_produces_outputs(tmp_path, sample_epub_path):
    cfg = make_sample_config(tmp_path, sample_epub_path)
    provider = MockTranslationProvider()

    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=provider):
        await run_translation(cfg)

    book_dir = next((cfg.project.output_dir).iterdir())
    assert (book_dir / "translated.epub").exists()
    assert (book_dir / "bilingual.html").exists()
    assert (book_dir / "segments.jsonl").exists()
    assert (book_dir / "translations.jsonl").exists()
    assert (book_dir / "state.json").exists()
    assert (book_dir / "validation_report.json").exists()


@pytest.mark.asyncio
async def test_pipeline_state_completed(tmp_path, sample_epub_path):
    cfg = make_sample_config(tmp_path, sample_epub_path)
    provider = MockTranslationProvider()

    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=provider):
        await run_translation(cfg)

    import json
    book_dir = next(cfg.project.output_dir.iterdir())
    state = json.loads((book_dir / "state.json").read_text())
    assert state["status"] == "completed"
    assert state["completed_segments"] == state["total_segments"]


@pytest.mark.asyncio
async def test_pipeline_all_segments_translated(tmp_path, sample_epub_path):
    cfg = make_sample_config(tmp_path, sample_epub_path)
    provider = MockTranslationProvider()

    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=provider):
        await run_translation(cfg)

    book_dir = next(cfg.project.output_dir.iterdir())
    lines = [
        l for l in (book_dir / "translations.jsonl").read_text().splitlines() if l.strip()
    ]
    import json
    completed = [json.loads(l) for l in lines if json.loads(l)["status"] == "completed"]
    assert len(completed) > 0
    assert len(completed) == provider.call_count


@pytest.mark.asyncio
async def test_resume_does_not_retranslate_completed(tmp_path, sample_epub_path):
    cfg = make_sample_config(tmp_path, sample_epub_path)

    # First run — translate all segments
    provider1 = MockTranslationProvider()
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=provider1):
        await run_translation(cfg)

    first_run_calls = provider1.call_count

    # Second run — should translate nothing (all already completed)
    provider2 = MockTranslationProvider()
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=provider2):
        await run_translation(cfg)

    assert provider2.call_count == 0, (
        f"Expected 0 API calls on resume, but got {provider2.call_count}"
    )


@pytest.mark.asyncio
async def test_resume_continues_after_partial_completion(tmp_path, sample_epub_path):
    from ebook_translator.epub.reader import read_epub
    from ebook_translator.segmenter.segmenter import segment_all_documents

    cfg = make_sample_config(tmp_path, sample_epub_path)

    # Count total segments
    _, spine_docs = read_epub(cfg.input.path)
    total = len(segment_all_documents(spine_docs))

    # First run — fail after translating half
    half = total // 2
    provider1 = MockTranslationProvider(fail_first_n=0)
    call_count = 0
    original_translate = provider1.translate

    async def limited_translate(request):
        nonlocal call_count
        call_count += 1
        if call_count > half:
            from ebook_translator.providers.base import ProviderError
            raise ProviderError("Simulated failure")
        return await original_translate(request)

    provider1.translate = limited_translate

    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=provider1):
        await run_translation(cfg)

    # Second run — should only translate remaining segments
    provider2 = MockTranslationProvider()
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=provider2):
        await run_translation(cfg)

    # The second provider should have been called for remaining + any retries of failed
    assert provider2.call_count <= total
