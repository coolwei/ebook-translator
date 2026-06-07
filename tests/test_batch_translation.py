from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ebook_translator.batch_parser import parse_batch_response
from ebook_translator.batch_planner import plan_translation_batches
from ebook_translator.checkpoint import CheckpointManager
from ebook_translator.config import ProviderConfig, TranslationConfig
from ebook_translator.models import Segment
from ebook_translator.providers.base import (
    ProviderError,
    TranslationProvider,
    TranslationRequest,
    TranslationResponse,
)
from ebook_translator.translator import run_translation
from tests.conftest import MockTranslationProvider, make_sample_config, make_sample_epub


def _make_segments(n: int, char_len: int = 20) -> list[Segment]:
    segments = []
    for i in range(n):
        text = f"Segment {i} " + ("x" * max(char_len - len(f"Segment {i} "), 0))
        segments.append(
            Segment(
                segment_id=f"0000:{i:04d}:hash{i:04d}",
                chapter_index=0,
                block_index=i,
                sha1_prefix=f"hash{i:04d}",
                source_text=text,
                source_html=text,
                tag_name="p",
                chapter_href="chap01.xhtml",
            )
        )
    return segments


@pytest.mark.asyncio
async def test_default_segment_mode_one_request_per_segment(tmp_path, sample_epub_path):
    cfg = make_sample_config(tmp_path, sample_epub_path)
    provider = MockTranslationProvider()
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=provider):
        await run_translation(cfg)
    assert provider.call_count == 5


def test_plan_batches_groups_five_segments():
    segs = _make_segments(5)
    batches = plan_translation_batches(
        segs, segments_per_request=5, max_chars_per_request=6000
    )
    assert len(batches) == 1
    assert len(batches[0].segments) == 5


def test_plan_batches_splits_on_char_limit():
    segs = _make_segments(5, char_len=2000)
    batches = plan_translation_batches(
        segs, segments_per_request=5, max_chars_per_request=3000
    )
    assert len(batches) >= 2
    assert all(len(b.segments) <= 5 for b in batches)


def test_parse_batch_response_valid():
    raw = json.dumps(
        [
            {"segment_id": "a", "translation": "譯文A"},
            {"segment_id": "b", "translation": "譯文B"},
        ],
        ensure_ascii=False,
    )
    result = parse_batch_response(raw, {"a", "b"})
    assert result.parse_error is None
    assert result.missing_ids == []
    assert result.translations == {"a": "譯文A", "b": "譯文B"}


def test_parse_batch_response_missing_id():
    raw = json.dumps([{"segment_id": "a", "translation": "譯文A"}])
    result = parse_batch_response(raw, {"a", "b"})
    assert "b" in result.missing_ids


def test_parse_batch_response_unknown_id():
    raw = json.dumps([{"segment_id": "z", "translation": "譯文"}])
    result = parse_batch_response(raw, {"a"})
    assert result.parse_error and "unknown" in result.parse_error


def test_parse_batch_response_duplicate_id():
    raw = json.dumps(
        [
            {"segment_id": "a", "translation": "1"},
            {"segment_id": "a", "translation": "2"},
        ]
    )
    result = parse_batch_response(raw, {"a"})
    assert result.parse_error and "duplicate" in result.parse_error


def test_parse_batch_strips_markdown_fence():
    raw = '```json\n[{"segment_id": "a", "translation": "譯文"}]\n```'
    result = parse_batch_response(raw, {"a"})
    assert result.translations["a"] == "譯文"


class BatchAwareProvider(MockTranslationProvider):
    pass


@pytest.mark.asyncio
async def test_segment_batch_mode_fewer_requests(tmp_path, sample_epub_path):
    cfg = make_sample_config(tmp_path, sample_epub_path)
    cfg.translation = TranslationConfig(
        mode="segment_batch",
        segments_per_request=5,
        max_chars_per_request=6000,
    )
    provider = BatchAwareProvider()
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=provider):
        await run_translation(cfg)
    assert provider.call_count == 1
    assert all(
        call.user_message.startswith("請翻譯以下多個段落") for call in provider.calls
    )


@pytest.mark.asyncio
async def test_batch_cache_hit_skips_api(tmp_path):
    from tests.test_cache import make_dup_epub

    dup = make_dup_epub(tmp_path)
    cfg = make_sample_config(tmp_path, dup)
    cfg.translation = TranslationConfig(
        mode="segment_batch",
        segments_per_request=5,
        max_chars_per_request=6000,
    )
    provider = BatchAwareProvider()
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=provider):
        cfg.translation.mode = "segment"
        await run_translation(cfg, limit=1)
        cfg.translation.mode = "segment_batch"
        await run_translation(cfg)

    book_dir = next(cfg.project.output_dir.iterdir())
    records = CheckpointManager(book_dir).load_all_translations()
    assert provider.call_count == 2
    assert any(r.reused_from_segment_id for r in records.values())


class PartialBatchProvider(TranslationProvider):
    def __init__(self, warn_segment_id: str) -> None:
        self.warn_segment_id = warn_segment_id
        self.call_count = 0

    async def translate(self, request: TranslationRequest) -> TranslationResponse:
        self.call_count += 1
        payload = json.loads(request.user_message[request.user_message.index("[") :])
        items = []
        for item in payload:
            sid = item["segment_id"]
            if sid == self.warn_segment_id:
                text = "美国軍隊"
            else:
                text = f"這是已翻譯（{sid}）"
            items.append({"segment_id": sid, "translation": text})
        return TranslationResponse(
            translated_text=json.dumps(items, ensure_ascii=False),
            model=request.model,
            finish_reason="stop",
            prompt_tokens=1,
            completion_tokens=1,
        )

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_batch_single_warning_does_not_fail_others(tmp_path, sample_epub_path):
    from ebook_translator.epub.reader import read_epub
    from ebook_translator.segmenter.segmenter import segment_all_documents

    cfg = make_sample_config(tmp_path, sample_epub_path)
    cfg.quality.strict_mode = False
    cfg.translation = TranslationConfig(
        mode="segment_batch",
        segments_per_request=5,
        max_chars_per_request=6000,
    )
    _, spine = read_epub(cfg.input.path)
    warn_id = segment_all_documents(spine)[1].segment_id
    provider = PartialBatchProvider(warn_segment_id=warn_id)
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=provider):
        await run_translation(cfg)

    book_dir = next(cfg.project.output_dir.iterdir())
    records = CheckpointManager(book_dir).load_all_translations()
    completed = [r for r in records.values() if r.status == "completed"]
    warned = [r for r in completed if r.quality_warnings]
    assert len(completed) == 5
    assert len(warned) == 1


class FenceBatchProvider(TranslationProvider):
    def __init__(self) -> None:
        self.call_count = 0

    async def translate(self, request: TranslationRequest) -> TranslationResponse:
        self.call_count += 1
        payload = json.loads(request.user_message[request.user_message.index("[") :])
        items = []
        for i, item in enumerate(payload):
            sid = item["segment_id"]
            text = "```\n壞掉\n```" if i == 0 else f"這是已翻譯（{sid}）"
            items.append({"segment_id": sid, "translation": text})
        return TranslationResponse(
            translated_text=json.dumps(items, ensure_ascii=False),
            model=request.model,
            finish_reason="stop",
            prompt_tokens=1,
            completion_tokens=1,
        )

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_batch_single_hard_fail_only_affects_that_segment(tmp_path, sample_epub_path):
    cfg = make_sample_config(tmp_path, sample_epub_path)
    cfg.translation = TranslationConfig(
        mode="segment_batch",
        segments_per_request=5,
        max_chars_per_request=6000,
    )
    provider = FenceBatchProvider()
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=provider):
        await run_translation(cfg)

    book_dir = next(cfg.project.output_dir.iterdir())
    records = list(CheckpointManager(book_dir).load_all_translations().values())
    assert sum(1 for r in records if r.status == "quality_failed") == 1
    assert sum(1 for r in records if r.status == "completed") == 4


class FailingThenOkBatchProvider(TranslationProvider):
    def __init__(self) -> None:
        self.call_count = 0

    async def translate(self, request: TranslationRequest) -> TranslationResponse:
        self.call_count += 1
        if request.model == "primary-model":
            raise ProviderError("Provider returned empty message content")
        return await MockTranslationProvider().translate(request)

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_batch_request_failure_falls_back(tmp_path, sample_epub_path):
    cfg = make_sample_config(tmp_path, sample_epub_path)
    cfg.translation = TranslationConfig(
        mode="segment_batch",
        segments_per_request=5,
        max_chars_per_request=6000,
    )
    cfg.provider = ProviderConfig(
        base_url="https://api.example.com/v1",
        api_key_env="FAKE_API_KEY",
        model="primary-model",
        fallback_models=["fallback-model-1"],
        api_key="test-key",
    )
    provider = FailingThenOkBatchProvider()
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=provider):
        await run_translation(cfg)

    book_dir = next(cfg.project.output_dir.iterdir())
    records = CheckpointManager(book_dir).load_all_translations()
    assert all(r.status == "completed" for r in records.values())
    assert provider.call_count == 2


@pytest.mark.asyncio
async def test_failed_retry_uses_single_segment_mode(tmp_path, sample_epub_path):
    from tests.test_fallback import ModelAwareProvider

    cfg = make_sample_config(tmp_path, sample_epub_path)
    cfg.translation.mode = "segment"
    provider = ModelAwareProvider(fail_models={"test-model"}, fail_with="empty")
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=provider):
        await run_translation(cfg, limit=1)

    cfg.translation = TranslationConfig(
        mode="segment_batch",
        segments_per_request=5,
        max_chars_per_request=6000,
    )
    provider2 = MockTranslationProvider()
    with patch("ebook_translator.translator.OpenAICompatibleProvider", return_value=provider2):
        await run_translation(cfg, failed_only=True)

    assert provider2.call_count >= 1
    for call in provider2.calls:
        assert not call.user_message.startswith("請翻譯以下多個段落")
