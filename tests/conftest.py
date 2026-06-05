from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import ebooklib
from ebooklib import epub

from ebook_translator.config import (
    AppConfig,
    ContextConfig,
    InputConfig,
    LimitsConfig,
    LoggingConfig,
    ProjectConfig,
    ProviderConfig,
    QualityConfig,
    ResumeConfig,
    TranslationConfig,
)
from ebook_translator.models import Segment
from ebook_translator.providers.base import (
    AuthError,
    ProviderError,
    RateLimitError,
    TranslationProvider,
    TranslationRequest,
    TranslationResponse,
)


# ---------------------------------------------------------------------------
# Minimal EPUB factory
# ---------------------------------------------------------------------------

def make_sample_epub(path: Path) -> Path:
    book = epub.EpubBook()
    book.set_title("Test Book")
    book.set_language("en")

    ch1 = epub.EpubHtml(title="Chapter 1", file_name="chap01.xhtml", lang="en")
    ch1.set_content(
        b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Chapter 1</title></head>
<body>
  <h1>Introduction</h1>
  <p>The quick brown fox jumps over the lazy dog.</p>
  <p>Pack my box with five dozen liquor jugs.</p>
</body>
</html>"""
    )

    ch2 = epub.EpubHtml(title="Chapter 2", file_name="chap02.xhtml", lang="en")
    ch2.set_content(
        b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Chapter 2</title></head>
<body>
  <h1>Conclusion</h1>
  <p>How vexingly quick daft zebras jump.</p>
</body>
</html>"""
    )

    book.add_item(ch1)
    book.add_item(ch2)
    book.spine = ["nav", ch1, ch2]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    out = path / "sample.epub"
    epub.write_epub(str(out), book, {})
    return out


@pytest.fixture
def sample_epub_path(tmp_path: Path) -> Path:
    return make_sample_epub(tmp_path)


# ---------------------------------------------------------------------------
# Mock provider
# ---------------------------------------------------------------------------

class MockTranslationProvider(TranslationProvider):
    def __init__(
        self,
        *,
        fail_first_n: int = 0,
        fail_with: type[Exception] = RateLimitError,
        translation_fn=None,
    ) -> None:
        self._fail_first_n = fail_first_n
        self._fail_with = fail_with
        self._translation_fn = translation_fn
        self.call_count = 0
        self.calls: list[TranslationRequest] = []

    async def translate(self, request: TranslationRequest) -> TranslationResponse:
        self.call_count += 1
        self.calls.append(request)

        if self.call_count <= self._fail_first_n:
            raise self._fail_with("mock error")

        # Clean Traditional-Chinese output that passes all quality gates
        # (CJK-dominant, no Simplified chars, no prefix/fence/explanation).
        # Include the sha1_prefix for per-segment uniqueness while keeping the
        # ASCII-letter ratio well below the 0.75 untranslated_text threshold.
        text = (
            self._translation_fn(request.user_message)
            if self._translation_fn
            else f"這是已翻譯的內容（{request.segment.sha1_prefix}）"
        )
        return TranslationResponse(
            translated_text=text,
            model=request.model,
            finish_reason="stop",
            prompt_tokens=10,
            completion_tokens=10,
        )

    async def close(self) -> None:
        pass


@pytest.fixture
def mock_provider() -> MockTranslationProvider:
    return MockTranslationProvider()


# ---------------------------------------------------------------------------
# Sample config factory
# ---------------------------------------------------------------------------

def make_sample_config(tmp_path: Path, epub_path: Path | None = None) -> AppConfig:
    return AppConfig(
        project=ProjectConfig(output_dir=tmp_path / "outputs"),
        input=InputConfig(path=epub_path or tmp_path / "sample.epub"),
        translation=TranslationConfig(),
        provider=ProviderConfig(
            base_url="https://api.example.com/v1",
            api_key_env="FAKE_API_KEY",
            model="test-model",
            api_key="test-key",
        ),
        limits=LimitsConfig(rpm=60, concurrency=2),
        resume=ResumeConfig(max_retries=2),
        logging=LoggingConfig(level="debug"),
        quality=QualityConfig(),
        context=ContextConfig(previous_segments=1),
    )


@pytest.fixture
def sample_config(tmp_path: Path, sample_epub_path: Path) -> AppConfig:
    return make_sample_config(tmp_path, sample_epub_path)


# ---------------------------------------------------------------------------
# Sample segments fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_segments() -> list[Segment]:
    return [
        Segment(
            segment_id="0000:0000:aabbccdd",
            chapter_index=0,
            block_index=0,
            sha1_prefix="aabbccdd",
            source_text="The quick brown fox jumps over the lazy dog.",
            source_html="The quick brown fox jumps over the lazy dog.",
            tag_name="p",
            chapter_href="chap01.xhtml",
        ),
        Segment(
            segment_id="0000:0001:11223344",
            chapter_index=0,
            block_index=1,
            sha1_prefix="11223344",
            source_text="Pack my box with five dozen liquor jugs.",
            source_html="Pack my box with five dozen liquor jugs.",
            tag_name="p",
            chapter_href="chap01.xhtml",
        ),
        Segment(
            segment_id="0001:0000:55667788",
            chapter_index=1,
            block_index=0,
            sha1_prefix="55667788",
            source_text="How vexingly quick daft zebras jump.",
            source_html="How vexingly quick daft zebras jump.",
            tag_name="p",
            chapter_href="chap02.xhtml",
        ),
    ]
