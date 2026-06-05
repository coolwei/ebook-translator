from __future__ import annotations

import asyncio

import pytest

from ebook_translator.config import LimitsConfig
from ebook_translator.providers.base import AuthError, FatalProviderError, ProviderError, RateLimitError, TranslationResponse
from ebook_translator.scheduler import RateLimiter, TranslationScheduler
from tests.conftest import MockTranslationProvider, make_sample_config


def make_request():
    from ebook_translator.models import Segment
    from ebook_translator.providers.base import TranslationRequest
    seg = Segment(
        segment_id="0000:0000:aabb",
        chapter_index=0,
        block_index=0,
        sha1_prefix="aabb",
        source_text="Test.",
        source_html="Test.",
        tag_name="p",
        chapter_href="chap01.xhtml",
    )
    return TranslationRequest(
        segment=seg,
        system_prompt="Translate.",
        user_message="Test.",
        model="test-model",
        max_tokens=100,
    )


@pytest.mark.asyncio
async def test_successful_translation_through_scheduler():
    provider = MockTranslationProvider()
    scheduler = TranslationScheduler(provider, LimitsConfig(rpm=60, concurrency=2), max_retries=2)
    response = await scheduler.translate(make_request())
    assert "（aabb）" in response.translated_text  # sha1_prefix from make_request()


@pytest.mark.asyncio
async def test_retries_on_rate_limit_error():
    provider = MockTranslationProvider(fail_first_n=2, fail_with=RateLimitError)
    scheduler = TranslationScheduler(provider, LimitsConfig(rpm=60, concurrency=2), max_retries=3)
    response = await scheduler.translate(make_request())
    assert provider.call_count == 3
    assert "（aabb）" in response.translated_text  # sha1_prefix from make_request()


@pytest.mark.asyncio
async def test_retries_on_provider_error():
    provider = MockTranslationProvider(fail_first_n=2, fail_with=ProviderError)
    scheduler = TranslationScheduler(provider, LimitsConfig(rpm=60, concurrency=2), max_retries=3)
    response = await scheduler.translate(make_request())
    assert provider.call_count == 3
    assert response.translated_text


@pytest.mark.asyncio
async def test_auth_error_propagates_immediately():
    provider = MockTranslationProvider(fail_first_n=99, fail_with=AuthError)
    scheduler = TranslationScheduler(provider, LimitsConfig(rpm=60, concurrency=2), max_retries=3)
    with pytest.raises(AuthError):
        await scheduler.translate(make_request())
    assert provider.call_count == 1


@pytest.mark.asyncio
async def test_fatal_provider_error_propagates_immediately():
    provider = MockTranslationProvider(fail_first_n=99, fail_with=FatalProviderError)
    scheduler = TranslationScheduler(provider, LimitsConfig(rpm=60, concurrency=2), max_retries=3)
    with pytest.raises(FatalProviderError):
        await scheduler.translate(make_request())
    assert provider.call_count == 1


@pytest.mark.asyncio
async def test_max_retries_respected():
    provider = MockTranslationProvider(fail_first_n=99, fail_with=RateLimitError)
    scheduler = TranslationScheduler(provider, LimitsConfig(rpm=60, concurrency=2), max_retries=2)
    with pytest.raises(RateLimitError):
        await scheduler.translate(make_request())
    assert provider.call_count == 2


@pytest.mark.asyncio
async def test_concurrency_limit():
    import time
    results = []

    class SlowProvider(MockTranslationProvider):
        async def translate(self, request):
            results.append("start")
            await asyncio.sleep(0.05)
            results.append("end")
            return await super().translate(request)

    provider = SlowProvider()
    scheduler = TranslationScheduler(provider, LimitsConfig(rpm=60, concurrency=1), max_retries=1)

    # With concurrency=1, tasks must serialize
    await asyncio.gather(
        scheduler.translate(make_request()),
        scheduler.translate(make_request()),
    )
    # Should interleave as: start, end, start, end (not start, start, end, end)
    assert results == ["start", "end", "start", "end"]


@pytest.mark.asyncio
async def test_rate_limiter_allows_under_limit():
    limiter = RateLimiter(rpm=60)
    # Should not block for first 60 requests in theory; just test a few
    for _ in range(5):
        await limiter.acquire()  # should complete quickly
