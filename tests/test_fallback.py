from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ebook_translator.config import ProviderConfig
from ebook_translator.epub.reader import read_epub
from ebook_translator.providers.base import (
    ProviderError,
    RateLimitError,
    TranslationProvider,
    TranslationRequest,
    TranslationResponse,
)
from ebook_translator.segmenter.segmenter import segment_all_documents
from ebook_translator.translator import run_translation
from tests.conftest import make_sample_config


class ModelAwareProvider(TranslationProvider):
    """Fails for configured models; succeeds with clean Traditional output otherwise."""

    def __init__(
        self,
        *,
        fail_models: set[str] | None = None,
        fail_with: str = "empty",
        quality_bad_models: set[str] | None = None,
    ) -> None:
        self.fail_models = fail_models or set()
        self.quality_bad_models = quality_bad_models or set()
        self.fail_with = fail_with
        self.call_count = 0
        self.models_used: list[str] = []

    async def translate(self, request: TranslationRequest) -> TranslationResponse:
        self.call_count += 1
        self.models_used.append(request.model)

        if request.model in self.fail_models:
            if self.fail_with == "rate_limit":
                raise RateLimitError("Rate limit exceeded: openai_error")
            if self.fail_with == "empty":
                raise ProviderError("Provider returned empty message content")
            raise ProviderError(self.fail_with)

        if request.model in self.quality_bad_models:
            text = "这是简体譯文。"
        else:
            text = f"這是已翻譯的內容（{request.segment.sha1_prefix}）"

        return TranslationResponse(
            translated_text=text,
            model=request.model,
            finish_reason="stop",
            prompt_tokens=1,
            completion_tokens=1,
        )

    async def close(self) -> None:
        pass


def _config_with_fallback(tmp_path: Path, epub_path: Path) -> object:
    cfg = make_sample_config(tmp_path, epub_path)
    cfg.provider = ProviderConfig(
        base_url="https://api.example.com/v1",
        api_key_env="FAKE_API_KEY",
        model="primary-model",
        fallback_models=["fallback-model-1", "fallback-model-2"],
        api_key="test-key",
    )
    cfg.limits.concurrency = 1
    return cfg


def _load_records(book_dir: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (book_dir / "translations.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.mark.asyncio
async def test_config_reads_fallback_models(tmp_path, sample_epub_path, monkeypatch):
    import yaml
    from ebook_translator.config import load_config

    data = {
        "input": {"path": str(sample_epub_path)},
        "provider": {
            "base_url": "https://api.example.com/v1",
            "api_key_env": "TEST_API_KEY",
            "model": "primary-model",
            "fallback_models": ["fb-1", "fb-2"],
        },
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(data), encoding="utf-8")
    monkeypatch.setenv("TEST_API_KEY", "sk-test")
    cfg = load_config(path)
    assert cfg.provider.model == "primary-model"
    assert cfg.provider.fallback_models == ["fb-1", "fb-2"]


@pytest.mark.asyncio
async def test_no_fallback_preserves_old_behavior(tmp_path, sample_epub_path):
    cfg = make_sample_config(tmp_path, sample_epub_path)
    provider = ModelAwareProvider(fail_models={"test-model"}, fail_with="empty")
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=provider):
        await run_translation(cfg)

    book_dir = next(cfg.project.output_dir.iterdir())
    records = _load_records(book_dir)
    assert all(r["status"] == "failed" for r in records)
    assert all(r["model"] == "test-model" for r in records)
    assert all("fallback_from" not in r or r.get("fallback_from") is None for r in records)


@pytest.mark.asyncio
async def test_primary_success_does_not_switch(tmp_path, sample_epub_path):
    cfg = _config_with_fallback(tmp_path, sample_epub_path)
    provider = ModelAwareProvider()
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=provider):
        await run_translation(cfg)

    assert all(model == "primary-model" for model in provider.models_used)


@pytest.mark.asyncio
async def test_empty_content_falls_back(tmp_path, sample_epub_path):
    cfg = _config_with_fallback(tmp_path, sample_epub_path)
    provider = ModelAwareProvider(fail_models={"primary-model"}, fail_with="empty")
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=provider):
        await run_translation(cfg)

    book_dir = next(cfg.project.output_dir.iterdir())
    records = _load_records(book_dir)
    assert all(r["status"] == "completed" for r in records)
    assert any(r.get("fallback_from") == "primary-model" for r in records)
    assert any(r.get("fallback_attempt") == 1 for r in records)


@pytest.mark.asyncio
async def test_rate_limit_falls_back(tmp_path, sample_epub_path):
    cfg = _config_with_fallback(tmp_path, sample_epub_path)
    provider = ModelAwareProvider(fail_models={"primary-model"}, fail_with="rate_limit")
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=provider):
        await run_translation(cfg)

    book_dir = next(cfg.project.output_dir.iterdir())
    records = _load_records(book_dir)
    assert all(r["status"] == "completed" for r in records)
    assert all(r["model"] == "fallback-model-1" for r in records)


@pytest.mark.asyncio
async def test_quality_failed_falls_back(tmp_path, sample_epub_path):
    cfg = _config_with_fallback(tmp_path, sample_epub_path)
    provider = ModelAwareProvider(quality_bad_models={"primary-model"})
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=provider):
        await run_translation(cfg)

    book_dir = next(cfg.project.output_dir.iterdir())
    records = _load_records(book_dir)
    assert all(r["status"] == "completed" for r in records)
    assert all(r["model"] == "fallback-model-1" for r in records)


@pytest.mark.asyncio
async def test_fallback_success_continues_on_next_segment(tmp_path, sample_epub_path):
    cfg = _config_with_fallback(tmp_path, sample_epub_path)
    _, spine = read_epub(cfg.input.path)
    segs = segment_all_documents(spine)
    first_id = segs[0].segment_id

    provider = ModelAwareProvider(fail_models={"primary-model"}, fail_with="empty")
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=provider):
        await run_translation(cfg, limit=2)

    # First segment: primary -> fallback-model-1
    first_calls = [m for i, m in enumerate(provider.models_used[:2])]
    assert first_calls[0] == "primary-model"
    assert first_calls[1] == "fallback-model-1"

    # Second segment should start with fallback-model-1, not primary-model
    second_segment_calls = provider.models_used[2:]
    assert second_segment_calls
    assert second_segment_calls[0] == "fallback-model-1"

    book_dir = next(cfg.project.output_dir.iterdir())
    records = {r["segment_id"]: r for r in _load_records(book_dir)}
    assert records[first_id]["fallback_from"] == "primary-model"


@pytest.mark.asyncio
async def test_all_models_fail_records_final_status(tmp_path, sample_epub_path):
    cfg = _config_with_fallback(tmp_path, sample_epub_path)
    provider = ModelAwareProvider(
        fail_models={"primary-model", "fallback-model-1", "fallback-model-2"},
        fail_with="empty",
    )
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=provider):
        await run_translation(cfg, limit=1)

    book_dir = next(cfg.project.output_dir.iterdir())
    records = _load_records(book_dir)
    assert len(records) == 1
    assert records[0]["status"] == "failed"
    assert records[0]["model"] == "fallback-model-2"


@pytest.mark.asyncio
async def test_cache_reuse_still_works_with_fallback(tmp_path):
    from tests.test_cache import make_dup_epub

    dup = make_dup_epub(tmp_path)
    cfg = _config_with_fallback(tmp_path, dup)
    provider = ModelAwareProvider(fail_models={"primary-model"}, fail_with="empty")
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=provider):
        await run_translation(cfg)

    book_dir = next(cfg.project.output_dir.iterdir())
    records = _load_records(book_dir)
    completed = [r for r in records if r["status"] == "completed"]
    assert len(completed) == 3
    reused = [r for r in records if r.get("reused_from_segment_id")]
    assert len(reused) == 1
    # First unique hash: primary fail + fallback success (2 calls).
    # Second unique hash: starts on fallback-model-1 after sticky switch (1 call).
    assert provider.call_count == 3


@pytest.mark.asyncio
async def test_all_models_quality_failed_records_quality_failed(tmp_path, sample_epub_path):
    cfg = _config_with_fallback(tmp_path, sample_epub_path)
    provider = ModelAwareProvider(
        quality_bad_models={"primary-model", "fallback-model-1", "fallback-model-2"},
    )
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=provider):
        await run_translation(cfg, limit=1)

    book_dir = next(cfg.project.output_dir.iterdir())
    records = _load_records(book_dir)
    assert len(records) == 1
    assert records[0]["status"] == "quality_failed"
    assert records[0]["model"] == "fallback-model-2"