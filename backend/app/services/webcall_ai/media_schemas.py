from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MockSTTInput:
    voice_session_id: int
    worker_id: str
    locale: str | None = None


@dataclass(frozen=True)
class MockSTTResult:
    text_redacted: str
    language: str
    confidence: int
    is_final: bool
    provider: str = "mock"
    event_count: int = 1


@dataclass(frozen=True)
class MockTTSInput:
    voice_session_id: int
    worker_id: str
    text_redacted: str
    language: str
    voice: str = "mock_support_voice"


@dataclass(frozen=True)
class MockTTSResult:
    provider: str
    voice: str
    language: str
    text_redacted: str
    synthesis_status: str
    audio_reference: str
    event_count: int = 1
