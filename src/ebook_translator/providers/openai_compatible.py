from __future__ import annotations

import httpx

from ..config import ProviderConfig
from .base import (
    AuthError,
    ContextLengthError,
    FatalProviderError,
    ProviderError,
    RateLimitError,
    TranslationProvider,
    TranslationRequest,
    TranslationResponse,
)

CONTEXT_LENGTH_PHRASES = (
    "context length",
    "context_length_exceeded",
    "maximum context",
    "too many tokens",
    "token limit",
)


class OpenAICompatibleProvider(TranslationProvider):
    def __init__(self, config: ProviderConfig) -> None:
        self._model = config.model
        self._client = httpx.AsyncClient(
            base_url=config.base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            },
            timeout=config.timeout_seconds,
        )

    async def translate(self, request: TranslationRequest) -> TranslationResponse:
        payload = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_message},
            ],
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }
        try:
            response = await self._client.post("/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            raise ProviderError(f"Request timed out: {exc}") from exc
        except httpx.RequestError as exc:
            raise ProviderError(f"Request error: {exc}") from exc

        if response.status_code != 200:
            raise self._parse_error(response.status_code, response)

        data = response.json()
        choice = data["choices"][0]
        content = choice.get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("Provider returned empty message content")
        usage = data.get("usage", {})
        return TranslationResponse(
            translated_text=content.strip(),
            model=data.get("model", request.model),
            finish_reason=choice.get("finish_reason", ""),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
        )

    def _parse_error(self, status_code: int, response: httpx.Response) -> ProviderError:
        try:
            body = response.json()
            message = body.get("error", {}).get("message", response.text)
        except Exception:
            message = response.text

        if status_code in (401, 403):
            return AuthError(f"Authentication failed ({status_code}): {message}")
        if status_code == 404:
            return FatalProviderError(f"Provider endpoint not found (404): {message}")
        if status_code == 429:
            return RateLimitError(f"Rate limit exceeded: {message}")
        if status_code == 400:
            low = message.lower()
            if any(phrase in low for phrase in CONTEXT_LENGTH_PHRASES):
                return ContextLengthError(f"Context length exceeded: {message}")
        return ProviderError(f"Provider error ({status_code}): {message}")

    async def close(self) -> None:
        await self._client.aclose()
