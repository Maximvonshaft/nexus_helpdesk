from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class STTResult:
    text: str
    language: str | None = None
    confidence: int | None = None


@dataclass(frozen=True)
class LLMResult:
    response_text: str
    intent: str
    handoff_required: bool = False
    handoff_reason: str | None = None


@dataclass(frozen=True)
class TTSResult:
    audio_bytes: bytes
    mime_type: str
    text: str


class STTProvider:
    provider_name = "base"

    def transcribe(self, audio: bytes, *, language: str | None = None) -> STTResult:
        raise NotImplementedError


class LLMProvider:
    provider_name = "base"

    def respond(self, text: str, *, language: str | None = None) -> LLMResult:
        raise NotImplementedError


class TTSProvider:
    provider_name = "base"

    def synthesize(self, text: str, *, language: str | None = None) -> TTSResult:
        raise NotImplementedError

