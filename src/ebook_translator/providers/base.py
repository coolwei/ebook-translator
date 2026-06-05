from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..models import Segment


@dataclass
class TranslationRequest:
    segment: Segment
    system_prompt: str
    user_message: str
    model: str
    max_tokens: int
    temperature: float = 0.2


@dataclass
class TranslationResponse:
    translated_text: str
    model: str
    finish_reason: str
    prompt_tokens: int
    completion_tokens: int


class ProviderError(Exception):
    pass


class RateLimitError(ProviderError):
    pass


class AuthError(ProviderError):
    pass


class FatalProviderError(ProviderError):
    pass


class ContextLengthError(ProviderError):
    pass


class TranslationProvider(ABC):
    @abstractmethod
    async def translate(self, request: TranslationRequest) -> TranslationResponse:
        ...

    async def close(self) -> None:
        pass
