from __future__ import annotations

from .base import TranslationProvider, TranslationRequest, TranslationResponse


class MockProvider(TranslationProvider):
    """Deterministic offline provider for smoke testing.

    Returns a translation that preserves the source HTML and prefixes a marker,
    so end-to-end flow (segment -> translate -> render -> export) can be exercised
    without calling a real API.
    """

    def __init__(self, prefix: str = "【譯】") -> None:
        self._prefix = prefix
        self.call_count = 0

    async def translate(self, request: TranslationRequest) -> TranslationResponse:
        self.call_count += 1
        text = f"{self._prefix}{request.segment.source_html}"
        return TranslationResponse(
            translated_text=text,
            model=f"mock:{request.model}",
            finish_reason="stop",
            prompt_tokens=len(request.user_message),
            completion_tokens=len(text),
        )

    async def close(self) -> None:
        pass
