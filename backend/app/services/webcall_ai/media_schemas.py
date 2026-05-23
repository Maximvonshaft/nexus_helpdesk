from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WebCallSTTInput:
    voice_session_id: int
    worker_id: str
    locale: str | None = None
    audio_reference: str | None = None


@dataclass(frozen=True)
class WebCallSTTResult:
    text_redacted: str | None
    language: str | None
    confidence: int | None
    is_final: bool
    provider: str
    event_count: int = 1
    status: str = "ok"
    error_code: str | None = None


@dataclass(frozen=True)
class WebCallTTSInput:
    voice_session_id: int
    worker_id: str
    text_redacted: str
    language: str
    voice: str = "mock_support_voice"


@dataclass(frozen=True)
class WebCallTTSResult:
    provider: str
    voice: str
    language: str
    text_redacted: str
    synthesis_status: str
    audio_reference: str | None
    event_count: int = 1
    error_code: str | None = None


MockSTTInput = WebCallSTTInput
MockSTTResult = WebCallSTTResult
MockTTSInput = WebCallTTSInput
MockTTSResult = WebCallTTSResult
