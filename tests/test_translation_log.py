from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from ebook_translator.config import LoggingConfig, ProviderConfig
from ebook_translator.translation_log import TranslationLogWriter, sanitize_log_text
from ebook_translator.translator import run_translation
from tests.conftest import MockTranslationProvider, make_sample_config


def test_sanitize_log_text_redacts_sensitive_values():
    raw = (
        "Bearer sk-secret123 Authorization: Bearer abc "
        "api_key=hidden https://private.example.com/v1 failed"
    )
    cleaned = sanitize_log_text(raw)
    assert "Bearer" not in cleaned
    assert "sk-secret123" not in cleaned
    assert "Authorization" not in cleaned
    assert "private.example.com" not in cleaned
    assert "failed" in cleaned


def test_translation_log_writer_creates_file(tmp_path):
    log_path = tmp_path / "logs" / "translation.log"
    writer = TranslationLogWriter([log_path], "INFO", "test-book")
    writer.log(
        segment_id="0001:0002:abc",
        model="primary-model",
        status="completed",
        duration_ms=1200,
    )
    content = log_path.read_text(encoding="utf-8")
    assert "segment=0001:0002:abc" in content
    assert "model=primary-model" in content
    assert "status=completed" in content
    assert "duration_ms=1200" in content


@pytest.mark.asyncio
async def test_pipeline_writes_translation_log(tmp_path, sample_epub_path):
    cfg = make_sample_config(tmp_path, sample_epub_path)
    cfg.logging = LoggingConfig(
        enabled=True,
        level="info",
        file=tmp_path / "global-translation.log",
        per_book=True,
    )
    provider = MockTranslationProvider()
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=provider):
        await run_translation(cfg)

    book_dir = next(cfg.project.output_dir.iterdir())
    per_book_log = book_dir / "translation.log"
    global_log = tmp_path / "global-translation.log"
    assert per_book_log.exists()
    assert global_log.exists()
    assert "status=completed" in per_book_log.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_translation_log_does_not_contain_secrets(tmp_path, sample_epub_path):
    from ebook_translator.providers.base import ProviderError, TranslationRequest, TranslationResponse

    class LeakyProvider(MockTranslationProvider):
        async def translate(self, request: TranslationRequest) -> TranslationResponse:
            self.call_count += 1
            if self.call_count == 1:
                raise ProviderError(
                    "Bearer sk-leaked-key Authorization: Bearer bad "
                    "https://secret-host.internal/v1 rate limit"
                )
            return TranslationResponse(
                translated_text=f"這是已翻譯的內容（{request.segment.sha1_prefix}）",
                model=request.model,
                finish_reason="stop",
                prompt_tokens=1,
                completion_tokens=1,
            )

    cfg = make_sample_config(tmp_path, sample_epub_path)
    cfg.provider = ProviderConfig(
        base_url="https://secret-host.internal/v1",
        api_key_env="FAKE_API_KEY",
        model="primary-model",
        fallback_models=["fallback-model-1"],
        api_key="sk-leaked-key",
    )
    cfg.logging = LoggingConfig(
        enabled=True,
        level="info",
        file=tmp_path / "secure.log",
        per_book=True,
    )
    provider = LeakyProvider()
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=provider):
        await run_translation(cfg, limit=1)

    log_text = (tmp_path / "secure.log").read_text(encoding="utf-8")
    assert "sk-leaked-key" not in log_text
    assert "Bearer" not in log_text
    assert "Authorization" not in log_text
    assert "secret-host.internal" not in log_text


@pytest.mark.asyncio
async def test_logging_disabled_skips_translation_log(tmp_path, sample_epub_path):
    cfg = make_sample_config(tmp_path, sample_epub_path)
    cfg.logging = LoggingConfig(enabled=False, file=tmp_path / "disabled.log", per_book=True)
    provider = MockTranslationProvider()
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=provider):
        await run_translation(cfg)

    book_dir = next(cfg.project.output_dir.iterdir())
    assert not (book_dir / "translation.log").exists()
    assert not (tmp_path / "disabled.log").exists()