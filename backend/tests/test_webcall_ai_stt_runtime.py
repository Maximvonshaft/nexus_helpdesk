import os
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webcall_ai_stt_runtime_tests.db")

import pytest

from app import models, operator_models, tool_models, voice_models, webchat_fast_models, webchat_models  # noqa: F401,E402
from app.db import Base, SessionLocal, engine
from app.services.webcall_ai.config import get_webcall_ai_settings
from app.services.webcall_ai.media_schemas import WebCallSTTInput, WebCallSTTResult
from app.services.webcall_ai.stt_runtime import run_stt_runtime_for_session
from app.utils.time import utc_now
from app.voice_models import WebchatVoiceSession, WebchatVoiceTranscriptSegment


class FakeSTTProvider:
    name = "fake_stt"

    def __init__(self, result: WebCallSTTResult | None = None):
        self.result = result or WebCallSTTResult(
            text_redacted="Runtime heard the customer.",
            language="en",
            confidence=96,
            is_final=True,
            provider=self.name,
            event_count=1,
        )
        self.inputs: list[WebCallSTTInput] = []

    def transcribe(self, input: WebCallSTTInput):
        self.inputs.append(input)
        return self.result


@pytest.fixture(autouse=True)
def clean_db_and_env(monkeypatch):
    get_webcall_ai_settings.cache_clear()
    monkeypatch.setenv("WEBCALL_AI_AGENT_ENABLED", "true")
    for key in [
        "APP_ENV",
        "WEBCALL_AI_STT_RUNTIME_ENABLED",
        "WEBCALL_AI_STT_RUNTIME_MODE",
        "WEBCALL_AI_STT_TRANSCRIPT_WRITE_ENABLED",
        "WEBCALL_AI_AUDIO_REFERENCE_SOURCE",
        "WEBCALL_AI_AUDIO_REFERENCE_STATIC_ENABLED",
        "WEBCALL_AI_AUDIO_REFERENCE_STATIC_URL",
        "WEBCALL_AI_AUDIO_REFERENCE_ALLOWLIST",
    ]:
        monkeypatch.delenv(key, raising=False)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    get_webcall_ai_settings.cache_clear()


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _voice_session(db) -> WebchatVoiceSession:
    now = utc_now()
    session = WebchatVoiceSession(
        public_id=f"voice_{uuid4().hex}",
        conversation_id=1,
        ticket_id=1,
        provider="livekit",
        provider_room_name=f"room_{uuid4().hex}",
        status="ringing",
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def test_mock_text_stt_runtime_writes_transcript_when_enabled(db, monkeypatch):
    session = _voice_session(db)
    monkeypatch.setenv("WEBCALL_AI_STT_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_STT_TRANSCRIPT_WRITE_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()
    provider = FakeSTTProvider()
    monkeypatch.setattr("app.services.webcall_ai.stt_runtime.get_stt_provider", lambda settings: provider)

    result = run_stt_runtime_for_session(
        db,
        session=session,
        worker_id="worker-a",
        participant_identity="visitor",
    )
    db.commit()

    assert result.usable is True
    assert result.text_redacted == "Runtime heard the customer."
    assert result.transcript_segment_id is not None
    assert result.stt_events == 1
    assert provider.inputs[0].audio_reference is None
    assert db.query(WebchatVoiceTranscriptSegment).count() == 1


def test_audio_reference_runtime_passes_controlled_reference(db, monkeypatch):
    session = _voice_session(db)
    monkeypatch.setenv("WEBCALL_AI_STT_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_STT_RUNTIME_MODE", "audio_reference")
    monkeypatch.setenv("WEBCALL_AI_AUDIO_REFERENCE_SOURCE", "static_fixture")
    monkeypatch.setenv("WEBCALL_AI_AUDIO_REFERENCE_STATIC_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_AUDIO_REFERENCE_STATIC_URL", "https://media.example.test/call.wav")
    get_webcall_ai_settings.cache_clear()
    provider = FakeSTTProvider()
    monkeypatch.setattr("app.services.webcall_ai.stt_runtime.get_stt_provider", lambda settings: provider)

    result = run_stt_runtime_for_session(
        db,
        session=session,
        worker_id="worker-a",
        participant_identity="visitor",
    )

    assert result.usable is True
    assert provider.inputs[0].audio_reference == "https://media.example.test/call.wav"


def test_audio_reference_runtime_without_reference_fails_safely(db, monkeypatch):
    session = _voice_session(db)
    monkeypatch.setenv("WEBCALL_AI_STT_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_STT_RUNTIME_MODE", "audio_reference")
    get_webcall_ai_settings.cache_clear()

    result = run_stt_runtime_for_session(
        db,
        session=session,
        worker_id="worker-a",
        participant_identity="visitor",
    )

    assert result.usable is False
    assert result.error_code == "stt_audio_reference_required"
    assert result.transcript_segment_id is None
    assert db.query(WebchatVoiceTranscriptSegment).count() == 0


def test_stt_provider_failure_writes_no_transcript(db, monkeypatch):
    session = _voice_session(db)
    monkeypatch.setenv("WEBCALL_AI_STT_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_STT_TRANSCRIPT_WRITE_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()
    provider = FakeSTTProvider(
        WebCallSTTResult(
            text_redacted=None,
            language="en",
            confidence=None,
            is_final=False,
            provider="fake_stt",
            event_count=0,
            status="unavailable",
            error_code="fake_stt_unavailable",
        )
    )
    monkeypatch.setattr("app.services.webcall_ai.stt_runtime.get_stt_provider", lambda settings: provider)

    result = run_stt_runtime_for_session(
        db,
        session=session,
        worker_id="worker-a",
        participant_identity="visitor",
    )

    assert result.usable is False
    assert result.error_code == "fake_stt_unavailable"
    assert db.query(WebchatVoiceTranscriptSegment).count() == 0
