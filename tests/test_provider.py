from __future__ import annotations

import json

import pytest
import pytest_httpx

from ebook_translator.config import ProviderConfig
from ebook_translator.models import Segment
from ebook_translator.providers.base import (
    AuthError,
    ContextLengthError,
    FatalProviderError,
    ProviderError,
    RateLimitError,
    TranslationRequest,
)
from ebook_translator.providers.openai_compatible import OpenAICompatibleProvider


def make_provider(base_url: str = "https://api.example.com/v1") -> OpenAICompatibleProvider:
    cfg = ProviderConfig(
        base_url=base_url,
        api_key_env="FAKE_KEY",
        model="test-model",
        api_key="sk-test",
    )
    return OpenAICompatibleProvider(cfg)


def make_request() -> TranslationRequest:
    seg = Segment(
        segment_id="0000:0000:aabb",
        chapter_index=0,
        block_index=0,
        sha1_prefix="aabb",
        source_text="Hello world.",
        source_html="Hello world.",
        tag_name="p",
        chapter_href="chap01.xhtml",
    )
    return TranslationRequest(
        segment=seg,
        system_prompt="Translate to zh-TW.",
        user_message="Hello world.",
        model="test-model",
        max_tokens=100,
    )


SUCCESSFUL_RESPONSE = {
    "id": "chatcmpl-abc",
    "object": "chat.completion",
    "model": "test-model",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "你好世界。"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
}


@pytest.mark.asyncio
async def test_successful_translation(httpx_mock):
    httpx_mock.add_response(
        url="https://api.example.com/v1/chat/completions",
        json=SUCCESSFUL_RESPONSE,
    )
    provider = make_provider()
    response = await provider.translate(make_request())
    assert response.translated_text == "你好世界。"
    assert response.model == "test-model"
    assert response.prompt_tokens == 10
    await provider.close()


@pytest.mark.asyncio
async def test_sends_correct_headers(httpx_mock):
    httpx_mock.add_response(json=SUCCESSFUL_RESPONSE)
    provider = make_provider()
    await provider.translate(make_request())
    request = httpx_mock.get_requests()[0]
    assert request.headers["authorization"] == "Bearer sk-test"
    assert "application/json" in request.headers["content-type"]
    await provider.close()


@pytest.mark.asyncio
async def test_401_raises_auth_error(httpx_mock):
    httpx_mock.add_response(
        status_code=401,
        json={"error": {"message": "Invalid API key"}},
    )
    provider = make_provider()
    with pytest.raises(AuthError):
        await provider.translate(make_request())
    await provider.close()


@pytest.mark.asyncio
async def test_403_raises_auth_error(httpx_mock):
    httpx_mock.add_response(
        status_code=403,
        json={"error": {"message": "Forbidden"}},
    )
    provider = make_provider()
    with pytest.raises(AuthError):
        await provider.translate(make_request())
    await provider.close()


@pytest.mark.asyncio
async def test_404_raises_fatal_provider_error(httpx_mock):
    httpx_mock.add_response(
        status_code=404,
        json={"error": {"message": "Not found"}},
    )
    provider = make_provider()
    with pytest.raises(FatalProviderError):
        await provider.translate(make_request())
    await provider.close()


@pytest.mark.asyncio
async def test_429_raises_rate_limit_error(httpx_mock):
    httpx_mock.add_response(
        status_code=429,
        json={"error": {"message": "Rate limit exceeded"}},
    )
    provider = make_provider()
    with pytest.raises(RateLimitError):
        await provider.translate(make_request())
    await provider.close()


@pytest.mark.asyncio
async def test_500_raises_provider_error(httpx_mock):
    httpx_mock.add_response(
        status_code=500,
        json={"error": {"message": "Internal server error"}},
    )
    provider = make_provider()
    with pytest.raises(ProviderError):
        await provider.translate(make_request())
    await provider.close()


@pytest.mark.asyncio
async def test_empty_message_content_raises_provider_error(httpx_mock):
    response = dict(SUCCESSFUL_RESPONSE)
    response["choices"] = [
        {
            "index": 0,
            "message": {"role": "assistant", "content": None},
            "finish_reason": "stop",
        }
    ]
    httpx_mock.add_response(json=response)
    provider = make_provider()
    with pytest.raises(ProviderError, match="empty message content"):
        await provider.translate(make_request())
    await provider.close()


@pytest.mark.asyncio
async def test_context_length_error(httpx_mock):
    httpx_mock.add_response(
        status_code=400,
        json={"error": {"message": "This model's maximum context length is 4096 tokens."}},
    )
    provider = make_provider()
    with pytest.raises(ContextLengthError):
        await provider.translate(make_request())
    await provider.close()
