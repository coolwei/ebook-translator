from __future__ import annotations

import asyncio

from .providers.base import (
    ContextLengthError,
    FatalProviderError,
    ProviderError,
    RateLimitError,
)

QUALITY_FALLBACK_CHECKS = frozenset({
    "simplified_chinese",
    "untranslated_text",
    "markdown_fence",
    "added_prefix",
    "explanation_prefix",
})

_ERROR_PHRASES = (
    "provider returned empty message content",
    "rate limit exceeded",
    "429",
    "openai_error",
    "timed out",
    "timeout",
    "provider error (5",
    "request timed out",
    "request error",
)


def is_quality_fallback_trigger(checks: str) -> bool:
    names = {part.strip() for part in checks.split(";") if part.strip()}
    return bool(names & QUALITY_FALLBACK_CHECKS)


def is_error_fallback_trigger(error: str) -> bool:
    low = error.lower()
    if "quality_failed" in low:
        return True
    return any(phrase in low for phrase in _ERROR_PHRASES)


def should_fallback_from_exception(exc: Exception) -> bool:
    if isinstance(exc, (ContextLengthError, FatalProviderError)):
        return False
    if isinstance(exc, (RateLimitError, ProviderError)):
        return is_error_fallback_trigger(str(exc))
    return is_error_fallback_trigger(str(exc))


def should_fallback_from_quality(checks: str) -> bool:
    return is_quality_fallback_trigger(checks)


class ModelFallbackState:
    """Tracks the active model across segments and builds the per-segment try chain."""

    def __init__(self, primary: str, fallback_models: list[str]) -> None:
        self._chain = [primary, *fallback_models]
        self._current = primary
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return len(self._chain) > 1

    @property
    def current_model(self) -> str:
        return self._current

    def models_to_try(self) -> list[str]:
        try:
            idx = self._chain.index(self._current)
        except ValueError:
            return [self._current]
        return self._chain[idx:]

    async def set_current(self, model: str) -> None:
        async with self._lock:
            self._current = model