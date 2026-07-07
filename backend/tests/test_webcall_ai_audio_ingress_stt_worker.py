import os
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webcall_ai_audio_ingress_stt_worker_tests.db")

import pytest

from app import models, operator_models, tool_models, voice_models, webchat_models  # noqa: F401,E402
from app.db import Base, SessionLocal, engine
from app.services.webcall_ai.config import get_webcall_ai_settings
from app.services.webcall_ai.deepgram_stt_provider import DeepgramSTTProvider
from app.services.webcall_ai.media_schemas import WebCallSTTInput, WebCallSTTResult
from app.utils.time import utc_now
from app.voice_models import WebchatVoiceAIAction, WebchatVoiceAITurn, WebchatVoiceSession, WebchatVoiceTranscriptSegment


class FakeDeepgramTransport:
    def __init__(self) -> None:
        self.calls = []

    def post_json(self, *, url: str, headers: dict[str, str], payload: dict[str, str], timeout_ms: int) -> dict:
        self.calls.append({"url": url, "headers": headers, "payload": payload, "timeout_ms": timeout_ms})
        return {
            "results": {
                "channels": [
                    {
                        "alternatives": [
                            {
                                "transcript": "Please track parcel ABC.",
                                "confidence": 0.91,
                            }
                        ]
                    }
                ]
            }
        }


class FailingSTTProvider:
    def transcribe(self, input: WebCallSTTInput):
        return WebCallSTTResult(
            text_redacted=None,
            language="en",
            confidence=None,
            is_final=False,
            provider="failing_stt",
            event_count=0,
            status="unavailable",
            error_code="failing_stt_unavailable",
        )


@pytest.fixture(autouse=True)
def clean_db_and_env(monkeypatch):
    get_webcall_ai_settings.cache_clear()
    monkeypatch.setenv("WEBCALL_AI_AGENT_ENABLED", "true")
    for key in [
        "APP_ENV",
        "WEBCALL_STT_PROVIDER",
        "WEBCALL_STT_DEEPGRAM_ENABLED",
        "WEBCALL_STT_TOKEN",
        "WEBCALL_AI_STT_RUNTIME_ENABLED",
        "WEBCALL_AI_STT_RUNTIME_MODE",
        "WEBCALL_AI_STT_TRANSCRIPT_WRITE_ENABLED",
        "WEBCALL_AI_AUDIO_REFERENCE_SOURCE",
        "WEBCALL_AI_AUDIO_REFERENCE_STATIC_URL",
        "WEBCALL_AI_AUDIO_REFERENCE_ALLOWLIST",
        "WEBCALL_AI_AUDIO_REFERENCE_STATIC_ENABLED",
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


def test_worker_default_path_unchanged(db):
    from app.services.webcall_ai.worker import run_webcall_ai_worker_once

    _voice_session(db)

    result = run_webcall_ai_worker_once(db, "worker-a", limit=10, lease_seconds=30)

    assert result == {
        "claimed": 1,
        "released": 1,
        "failed": 0,
        "skipped": 0,
        "turns": 1,
        "stt_events": 1,
        "tts_events": 1,
    }


def test_worker_mock_text_runtime_writes_transcript_and_turn(db, monkeypatch):
    from app.services.webcall_ai.worker import run_webcall_ai_worker_once

    session = _voice_session(db)
    monkeypatch.setenv("WEBCALL_AI_STT_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_STT_TRANSCRIPT_WRITE_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()

    result = run_webcall_ai_worker_once(db, "worker-a", limit=10, lease_seconds=30)
    db.refresh(session)
    turn = db.query(WebchatVoiceAITurn).one()
    transcript = db.query(WebchatVoiceTranscriptSegment).one()

    assert result["released"] == 1
    assert result["failed"] == 0
    assert result["turns"] == 1
    assert result["transcript_segments"] == 1
    assert result["stt_runtime_failures"] == 0
    assert turn.customer_text_redacted == "I want to check my parcel status."
    assert transcript.text_redacted == turn.customer_text_redacted
    assert session.ai_agent_status == "released"


def test_worker_audio_reference_deepgram_fake_transport_writes_transcript_and_turn(db, monkeypatch):
    from app.services.webcall_ai.worker import run_webcall_ai_worker_once

    _voice_session(db)
    monkeypatch.setenv("WEBCALL_AI_STT_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_STT_RUNTIME_MODE", "audio_reference")
    monkeypatch.setenv("WEBCALL_AI_STT_TRANSCRIPT_WRITE_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_AUDIO_REFERENCE_SOURCE", "static_fixture")
    monkeypatch.setenv("WEBCALL_AI_AUDIO_REFERENCE_STATIC_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_AUDIO_REFERENCE_STATIC_URL", "https://media.example.test/call.wav")
    monkeypatch.setenv("WEBCALL_STT_PROVIDER", "deepgram")
    monkeypatch.setenv("WEBCALL_STT_DEEPGRAM_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_STT_TOKEN", "local-token")
    get_webcall_ai_settings.cache_clear()
    settings = get_webcall_ai_settings()
    transport = FakeDeepgramTransport()
    provider = DeepgramSTTProvider(settings, transport=transport)
    monkeypatch.setattr("app.services.webcall_ai.stt_runtime.get_stt_provider", lambda settings: provider)

    result = run_webcall_ai_worker_once(db, "worker-a", limit=10, lease_seconds=30)
    turn = db.query(WebchatVoiceAITurn).one()
    transcript = db.query(WebchatVoiceTranscriptSegment).one()

    assert result["released"] == 1
    assert result["failed"] == 0
    assert result["transcript_segments"] == 1
    assert result["stt_runtime_failures"] == 0
    assert transport.calls[0]["payload"] == {"url": "https://media.example.test/call.wav"}
    assert turn.customer_text_redacted == "Please track parcel ABC."
    assert transcript.provider == "deepgram"
    assert transcript.text_redacted == "Please track parcel ABC."


def test_worker_deepgram_without_audio_reference_fails_safely_without_rows(db, monkeypatch):
    from app.services.webcall_ai.worker import run_webcall_ai_worker_once

    session = _voice_session(db)
    monkeypatch.setenv("WEBCALL_AI_STT_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_STT_RUNTIME_MODE", "audio_reference")
    monkeypatch.setenv("WEBCALL_AI_STT_TRANSCRIPT_WRITE_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_STT_PROVIDER", "deepgram")
    monkeypatch.setenv("WEBCALL_STT_DEEPGRAM_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_STT_TOKEN", "local-token")
    get_webcall_ai_settings.cache_clear()

    result = run_webcall_ai_worker_once(db, "worker-a", limit=10, lease_seconds=30)
    db.refresh(session)

    assert result["released"] == 0
    assert result["failed"] == 1
    assert result["turns"] == 0
    assert result["transcript_segments"] == 0
    assert result["stt_runtime_failures"] == 1
    assert session.ai_agent_status == "failed"
    assert db.query(WebchatVoiceTranscriptSegment).count() == 0
    assert db.query(WebchatVoiceAITurn).count() == 0
    assert db.query(WebchatVoiceAIAction).count() == 0


def test_worker_stt_provider_failure_writes_no_transcript_turn_or_action(db, monkeypatch):
    from app.services.webcall_ai.worker import run_webcall_ai_worker_once

    _voice_session(db)
    monkeypatch.setenv("WEBCALL_AI_STT_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_STT_TRANSCRIPT_WRITE_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()
    monkeypatch.setattr(
        "app.services.webcall_ai.stt_runtime.get_stt_provider",
        lambda settings: FailingSTTProvider(),
    )

    result = run_webcall_ai_worker_once(db, "worker-a", limit=10, lease_seconds=30)

    assert result["failed"] == 1
    assert result["turns"] == 0
    assert result["transcript_segments"] == 0
    assert result["stt_runtime_failures"] == 1
    assert db.query(WebchatVoiceTranscriptSegment).count() == 0
    assert db.query(WebchatVoiceAITurn).count() == 0
    assert db.query(WebchatVoiceAIAction).count() == 0
