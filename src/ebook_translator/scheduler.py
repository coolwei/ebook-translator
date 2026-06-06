from __future__ import annotations

import asyncio
import time
from collections import deque

from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from .config import LimitsConfig
from .providers.base import (
    AuthError,
    ContextLengthError,
    FatalProviderError,
    ProviderError,
    RateLimitError,
    TranslationProvider,
    TranslationRequest,
    TranslationResponse,
)


class RateLimiter:
    def __init__(self, rpm: int) -> None:
        self._rpm = rpm
        self._timestamps: deque[float] = deque()

    async def acquire(self) -> None:
        while True:
            now = time.monotonic()
            # Drop timestamps older than 60 seconds
            while self._timestamps and now - self._timestamps[0] >= 60.0:
                self._timestamps.popleft()

            if len(self._timestamps) < self._rpm:
                self._timestamps.append(now)
                return

            oldest = self._timestamps[0]
            sleep_for = 60.0 - (now - oldest) + 0.05
            await asyncio.sleep(max(sleep_for, 0.05))


class TranslationScheduler:
    def __init__(self, provider: TranslationProvider, config: LimitsConfig, max_retries: int = 3) -> None:
        self._provider = provider
        self._semaphore = asyncio.Semaphore(config.concurrency)
        self._rate_limiter = RateLimiter(config.rpm)
        self._max_retries = max_retries

    async def translate_once(self, request: TranslationRequest) -> TranslationResponse:
        async with self._semaphore:
            await self._rate_limiter.acquire()
            return await self._provider.translate(request)

    async def translate(self, request: TranslationRequest) -> TranslationResponse:
        async with self._semaphore:
            await self._rate_limiter.acquire()

            last_error: Exception | None = None
            try:
                async for attempt in AsyncRetrying(
                    retry=retry_if_exception(
                        lambda exc: isinstance(exc, (RateLimitError, ProviderError))
                        and not isinstance(exc, (AuthError, ContextLengthError, FatalProviderError))
                    ),
                    stop=stop_after_attempt(self._max_retries),
                    wait=wait_exponential(multiplier=1, min=2, max=60),
                    reraise=True,
                ):
                    with attempt:
                        return await self._provider.translate(request)
            except (AuthError, ContextLengthError, FatalProviderError):
                raise
            except (RateLimitError, ProviderError) as exc:
                raise
            # RetryError is raised if reraise=False, but we set reraise=True so the last exc propagates
        # unreachable but satisfies type checker
        raise RuntimeError("Translation scheduler reached unreachable state")
