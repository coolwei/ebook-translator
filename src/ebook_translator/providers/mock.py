from __future__ import annotations

from .base import TranslationProvider, TranslationRequest, TranslationResponse


class MockProvider(TranslationProvider):
    """Deterministic offline provider for smoke testing.

    Returns a translation that preserves the source HTML and appends a small
    suffix marker, so end-to-end flow (segment -> translate -> render -> export)
    can be exercised without calling a real API. The marker is a *suffix* and
    uses Traditional characters so it does not trip the quality validators
    (added prefix / simplified Chinese).
    """

    def __init__(self, suffix: str = "（譯）") -> None:
        self._suffix = suffix
        self.call_count = 0

    async def translate(self, request: TranslationRequest) -> TranslationResponse:
        self.call_count += 1
        text = f"{request.segment.source_html}{self._suffix}"
        return TranslationResponse(
            translated_text=text,
            model=f"mock:{request.model}",
            finish_reason="stop",
            prompt_tokens=len(request.user_message),
            completion_tokens=len(text),
        )

    async def close(self) -> None:
        pass
