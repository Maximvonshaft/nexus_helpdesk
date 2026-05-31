from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


class ProviderError(RuntimeError):
    def __init__(self, provider: str, code: str, message: str = "provider_error") -> None:
        super().__init__(message)
        self.provider = provider
        self.code = code


@dataclass(frozen=True)
class STTResult:
    text: str
    language: str | None = None
    confidence: int | None = None
    provider_name: str | None = None


@dataclass(frozen=True)
class LLMResult:
    response_text: str
    intent: str
    handoff_required: bool = False
    handoff_reason: str | None = None
    provider_name: str | None = None


@dataclass(frozen=True)
class TTSResult:
    audio_bytes: bytes
    mime_type: str
    text: str
    provider_name: str | None = None
    audio_chunks: tuple[Any, ...] = ()
    audio_stream: Iterable[Any] | None = None
    cancel_token: Any | None = None


class STTProvider:
    provider_name = "base"

    def transcribe(
        self,
        audio: bytes,
        *,
        language: str | None = None,
        sample_rate: int | None = None,
        channels: int | None = None,
        mime_type: str | None = None,
    ) -> STTResult:
        raise NotImplementedError


class LLMProvider:
    provider_name = "base"

    def respond(self, text: str, *, language: str | None = None) -> LLMResult:
        raise NotImplementedError


class TTSProvider:
    provider_name = "base"

    def synthesize(self, text: str, *, language: str | None = None) -> TTSResult:
        raise NotImplementedError
