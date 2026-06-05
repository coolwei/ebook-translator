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


# ---------------------------------------------------------------------------
# Phase 2.5: failed-only retry
# ---------------------------------------------------------------------------

class _SelectiveFailProvider(MockTranslationProvider):
    """Fails for a fixed set of segment_ids; succeeds for the rest."""

    def __init__(self, fail_ids, *, active=True):
        super().__init__()
        self._fail_ids = set(fail_ids)
        self._active = active
        self.seen_ids: list[str] = []

    async def translate(self, request):
        from ebook_translator.providers.base import ProviderError, TranslationResponse
        self.call_count += 1
        sid = request.segment.segment_id
        self.seen_ids.append(sid)
        if self._active and sid in self._fail_ids:
            raise ProviderError("Simulated failure")
        return TranslationResponse(
            translated_text=f"那是一個明亮的寒冷四月天。（{sid}）",
            model=request.model,
            finish_reason="stop",
            prompt_tokens=1,
            completion_tokens=1,
        )


@pytest.mark.asyncio
async def test_retry_failed_only_does_not_retranslate_completed(tmp_path, sample_epub_path):
    from ebook_translator.epub.reader import read_epub
    from ebook_translator.segmenter.segmenter import segment_all_documents

    cfg = make_sample_config(tmp_path, sample_epub_path)
    _, spine_docs = read_epub(cfg.input.path)
    segs = segment_all_documents(spine_docs)
    fail_ids = {segs[0].segment_id, segs[2].segment_id}

    # First run: two segments fail
    p1 = _SelectiveFailProvider(fail_ids, active=True)
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=p1):
        await run_translation(cfg)

    book_dir = next(cfg.project.output_dir.iterdir())

    # Confirm exactly those two failed
    import json
    completed_first = {
        json.loads(l)["segment_id"]
        for l in (book_dir / "translations.jsonl").read_text(encoding="utf-8").splitlines()
        if l.strip() and json.loads(l)["status"] == "completed"
    }
    assert fail_ids.isdisjoint(completed_first)

    # Retry-failed run: provider now succeeds for everything
    p2 = _SelectiveFailProvider(fail_ids, active=False)
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=p2):
        await run_translation(cfg, failed_only=True)

    # p2 must have been asked ONLY for the previously-failed segments
    assert set(p2.seen_ids) == fail_ids
    assert len(p2.seen_ids) == len(fail_ids)


@pytest.mark.asyncio
async def test_failed_then_success_makes_validate_pass(tmp_path, sample_epub_path):
    from ebook_translator.checkpoint import CheckpointManager
    from ebook_translator.config import QualityConfig
    from ebook_translator.epub.reader import read_epub
    from ebook_translator.segmenter.segmenter import segment_all_documents
    from ebook_translator.validator import validate_translations

    cfg = make_sample_config(tmp_path, sample_epub_path)
    _, spine_docs = read_epub(cfg.input.path)
    segs = segment_all_documents(spine_docs)
    fail_ids = {segs[0].segment_id, segs[2].segment_id}

    p1 = _SelectiveFailProvider(fail_ids, active=True)
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=p1):
        await run_translation(cfg)

    book_dir = next(cfg.project.output_dir.iterdir())

    # Before retry: validation reports failures
    ckpt = CheckpointManager(book_dir)
    segments = ckpt.load_segments()
    before = ckpt.load_all_translations()
    pairs_before = [(s, before[s.segment_id]) for s in segments if s.segment_id in before]
    report_before = validate_translations(pairs_before, QualityConfig())
    assert report_before.errors >= len(fail_ids)

    # Retry-failed succeeds
    p2 = _SelectiveFailProvider(fail_ids, active=False)
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=p2):
        await run_translation(cfg, failed_only=True)

    # After retry: the previously failed segments now validate clean
    after = ckpt.load_all_translations()
    pairs_after = [(s, after[s.segment_id]) for s in segments if s.segment_id in after]
    report_after = validate_translations(pairs_after, QualityConfig())
    failed_checks = [i for i in report_after.issues if i.check == "translation_failed"]
    assert failed_checks == []


# ---------------------------------------------------------------------------
# Phase 2.7: quality-failed retry
# ---------------------------------------------------------------------------

class _QualityProvider(MockTranslationProvider):
    """Returns quality-failing output (added prefix + Simplified) for a fixed set
    of segment_ids, clean Traditional output otherwise. When ``clean=True`` every
    segment gets clean output."""

    def __init__(self, bad_ids, *, clean=False):
        super().__init__()
        self._bad_ids = set(bad_ids)
        self._clean = clean
        self.seen_ids: list[str] = []

    async def translate(self, request):
        from ebook_translator.providers.base import TranslationResponse
        self.call_count += 1
        sid = request.segment.segment_id
        self.seen_ids.append(sid)
        if not self._clean and sid in self._bad_ids:
            text = "【第一章：开端】这是简体譯文。"  # added_prefix + simplified_chinese
        else:
            text = "這是乾淨的繁體譯文。"
        return TranslationResponse(
            translated_text=text,
            model=request.model,
            finish_reason="stop",
            prompt_tokens=1,
            completion_tokens=1,
        )


async def _seed_with_quality_failures(cfg, bad_ids):
    """Run a first full translation where bad_ids come back quality-failing."""
    p1 = _QualityProvider(bad_ids, clean=False)
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=p1):
        await run_translation(cfg)
    return p1


@pytest.mark.asyncio
async def test_quality_failed_only_retranslates_only_bad_segments(tmp_path, sample_epub_path):
    from ebook_translator.epub.reader import read_epub
    from ebook_translator.segmenter.segmenter import segment_all_documents

    cfg = make_sample_config(tmp_path, sample_epub_path)
    _, spine_docs = read_epub(cfg.input.path)
    segs = segment_all_documents(spine_docs)
    bad_ids = {segs[0].segment_id, segs[2].segment_id}
    clean_ids = {s.segment_id for s in segs} - bad_ids

    await _seed_with_quality_failures(cfg, bad_ids)

    # Quality-failed retry with a now-clean provider
    p2 = _QualityProvider(bad_ids, clean=True)
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=p2):
        await run_translation(cfg, quality_failed_only=True)

    # Only the quality-failing segments were re-translated...
    assert set(p2.seen_ids) == bad_ids
    # ...and no validate-clean completed segment was touched.
    assert clean_ids.isdisjoint(set(p2.seen_ids))


@pytest.mark.asyncio
async def test_quality_failed_retry_makes_validate_pass(tmp_path, sample_epub_path):
    from ebook_translator.checkpoint import CheckpointManager
    from ebook_translator.config import QualityConfig
    from ebook_translator.epub.reader import read_epub
    from ebook_translator.segmenter.segmenter import segment_all_documents
    from ebook_translator.validator import quality_failed_segment_ids

    cfg = make_sample_config(tmp_path, sample_epub_path)
    _, spine_docs = read_epub(cfg.input.path)
    segs = segment_all_documents(spine_docs)
    bad_ids = {segs[0].segment_id, segs[2].segment_id}

    await _seed_with_quality_failures(cfg, bad_ids)
    book_dir = next(cfg.project.output_dir.iterdir())
    ckpt = CheckpointManager(book_dir)
    segments = ckpt.load_segments()

    # Before retry: those segments are quality-failed
    before = ckpt.load_all_translations()
    pairs_before = [(s, before[s.segment_id]) for s in segments if s.segment_id in before]
    assert quality_failed_segment_ids(pairs_before, QualityConfig()) == bad_ids

    # Quality retry succeeds with clean output
    p2 = _QualityProvider(bad_ids, clean=True)
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=p2):
        await run_translation(cfg, quality_failed_only=True)

    # After retry: no quality failures remain (old bad records no longer pollute state)
    after = ckpt.load_all_translations()
    pairs_after = [(s, after[s.segment_id]) for s in segments if s.segment_id in after]
    assert quality_failed_segment_ids(pairs_after, QualityConfig()) == set()
    # The latest record for a fixed bad segment is the clean one (attempt incremented)
    latest = after[segs[0].segment_id]
    assert latest.status == "completed"
    assert latest.attempt == 2


@pytest.mark.asyncio
async def test_quality_failed_retry_still_flags_if_still_bad(tmp_path, sample_epub_path):
    """If the retry output is still quality-failing, the segment stays quality-failed."""
    from ebook_translator.checkpoint import CheckpointManager
    from ebook_translator.config import QualityConfig
    from ebook_translator.epub.reader import read_epub
    from ebook_translator.segmenter.segmenter import segment_all_documents
    from ebook_translator.validator import quality_failed_segment_ids

    cfg = make_sample_config(tmp_path, sample_epub_path)
    _, spine_docs = read_epub(cfg.input.path)
    segs = segment_all_documents(spine_docs)
    bad_ids = {segs[0].segment_id}

    await _seed_with_quality_failures(cfg, bad_ids)

    # Retry but provider STILL returns bad output
    p2 = _QualityProvider(bad_ids, clean=False)
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=p2):
        await run_translation(cfg, quality_failed_only=True)

    book_dir = next(cfg.project.output_dir.iterdir())
    ckpt = CheckpointManager(book_dir)
    segments = ckpt.load_segments()
    after = ckpt.load_all_translations()
    pairs_after = [(s, after[s.segment_id]) for s in segments if s.segment_id in after]
    # Still flagged as quality-failed
    assert bad_ids.issubset(quality_failed_segment_ids(pairs_after, QualityConfig()))
