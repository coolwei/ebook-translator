from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from ebooklib import epub

from ebook_translator.checkpoint import CheckpointManager
from ebook_translator.providers.base import ProviderError, TranslationResponse
from ebook_translator.translator import run_translation
from tests.conftest import MockTranslationProvider, make_sample_config


def make_dup_epub(tmp_path: Path) -> Path:
    """EPUB with two identical paragraphs (same source_hash) plus one unique one."""
    book = epub.EpubBook()
    book.set_identifier("dup-001")
    book.set_title("Dup Book")
    book.set_language("en")
    ch = epub.EpubHtml(title="C1", file_name="c1.xhtml", lang="en")
    ch.set_content(
        b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>C1</title></head>
<body>
  <p>Hello world.</p>
  <p>A unique line.</p>
  <p>Hello world.</p>
</body>
</html>"""
    )
    book.add_item(ch)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", ch]
    out = tmp_path / "dup.epub"
    epub.write_epub(str(out), book, {})
    return out


def _load_records(book_dir: Path) -> list[dict]:
    path = book_dir / "translations.jsonl"
    return [
        json.loads(l)
        for l in path.read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]


class SimplifiedProvider(MockTranslationProvider):
    """Always returns Simplified Chinese (trips the quality gate)."""

    async def translate(self, request):
        self.call_count += 1
        return TranslationResponse(
            translated_text="这是简体译文",  # 这/简/体 are Simplified
            model=request.model,
            finish_reason="stop",
            prompt_tokens=1,
            completion_tokens=1,
        )


class NoHtmlProvider(MockTranslationProvider):
    async def translate(self, request):
        self.call_count += 1
        return TranslationResponse(
            translated_text="translated text",
            model=request.model,
            finish_reason="stop",
            prompt_tokens=1,
            completion_tokens=1,
        )


def make_html_dup_epub(tmp_path: Path) -> Path:
    book = epub.EpubBook()
    book.set_identifier("html-dup-001")
    book.set_title("Html Dup Book")
    book.set_language("en")
    ch = epub.EpubHtml(title="C1", file_name="c1.xhtml", lang="en")
    ch.set_content(
        b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>C1</title></head>
<body>
  <p><em>Hello</em> world.</p>
  <p><em>Hello</em> world.</p>
</body>
</html>"""
    )
    book.add_item(ch)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", ch]
    out = tmp_path / "html-dup.epub"
    epub.write_epub(str(out), book, {})
    return out


@pytest.mark.asyncio
async def test_same_source_hash_calls_provider_once(tmp_path):
    dup = make_dup_epub(tmp_path)
    cfg = make_sample_config(tmp_path, dup)
    prov = MockTranslationProvider()
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=prov):
        await run_translation(cfg)

    # 3 segments, 2 distinct source hashes => provider hit twice, one reused.
    assert prov.call_count == 2


@pytest.mark.asyncio
async def test_cache_hit_writes_record_with_reuse_ref(tmp_path):
    dup = make_dup_epub(tmp_path)
    cfg = make_sample_config(tmp_path, dup)
    prov = MockTranslationProvider()
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=prov):
        await run_translation(cfg)

    book_dir = next(cfg.project.output_dir.iterdir())
    records = _load_records(book_dir)
    # All 3 segments have a completed record persisted.
    completed = [r for r in records if r["status"] == "completed"]
    assert len(completed) == 3
    # Exactly one of them is a cache reuse, pointing back at the original segment.
    reused = [r for r in records if r.get("reused_from_segment_id")]
    assert len(reused) == 1
    assert reused[0]["reused_from_segment_id"]
    assert reused[0]["reused_from_segment_id"] != reused[0]["segment_id"]


@pytest.mark.asyncio
async def test_failed_translation_not_reused(tmp_path):
    dup = make_dup_epub(tmp_path)
    cfg = make_sample_config(tmp_path, dup)
    cfg.resume.max_retries = 1  # don't retry, fail fast
    prov = MockTranslationProvider(fail_first_n=999, fail_with=ProviderError)
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=prov):
        await run_translation(cfg)

    book_dir = next(cfg.project.output_dir.iterdir())
    records = _load_records(book_dir)
    # Nothing was cached/reused from a failed translation.
    assert all(not r.get("reused_from_segment_id") for r in records)
    # Both identical segments were actually attempted (not short-circuited by cache).
    assert prov.call_count >= 3


@pytest.mark.asyncio
async def test_validate_dirty_completed_translation_not_reused(tmp_path):
    dup = make_html_dup_epub(tmp_path)
    cfg = make_sample_config(tmp_path, dup)
    prov = NoHtmlProvider()
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=prov):
        await run_translation(cfg)

    book_dir = next(cfg.project.output_dir.iterdir())
    records = _load_records(book_dir)
    assert len(records) == 2
    assert all(not r.get("reused_from_segment_id") for r in records)
    assert prov.call_count == 2


@pytest.mark.asyncio
async def test_quality_failed_translation_not_reused(tmp_path):
    dup = make_dup_epub(tmp_path)
    cfg = make_sample_config(tmp_path, dup)
    prov = SimplifiedProvider()
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=prov):
        await run_translation(cfg)

    book_dir = next(cfg.project.output_dir.iterdir())
    records = _load_records(book_dir)
    # Quality-failed translations must never be cached/reused.
    assert all(not r.get("reused_from_segment_id") for r in records)
    assert all(r["status"] == "quality_failed" for r in records)
    # Both identical segments were sent to the provider (no reuse of bad output).
    assert prov.call_count >= 3
