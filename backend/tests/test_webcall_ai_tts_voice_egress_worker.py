import os
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webcall_ai_tts_voice_egress_worker_tests.db")

import pytest

from app import models, operator_models, tool_models, voice_models, webchat_fast_models, webchat_models  # noqa: F401,E402
from app.db import Base, SessionLocal, engine
from app.services.webcall_ai.config import get_webcall_ai_settings
from app.services.webcall_ai.media_schemas import WebCallTTSInput, WebCallTTSResult
from app.services.webcall_ai.worker import run_webcall_ai_worker_once
from app.utils.time import utc_now
from app.voice_models import WebchatVoiceAIAction, WebchatVoiceAITurn, WebchatVoiceSession


class FailingTTSProvider:
    def synthesize(self, input: WebCallTTSInput):
        return WebCallTTSResult(
            provider="contract_stub",
            voice=input.voice,
            language=input.language,
            text_redacted=input.text_redacted,
            synthesis_status="unavailable",
            audio_reference=None,
            event_count=0,
            error_code="tts_contract_stub_not_implemented",
        )


@pytest.fixture(autouse=True)
def clean_db_and_env(monkeypatch):
    get_webcall_ai_settings.cache_clear()
    monkeypatch.setenv("WEBCALL_AI_AGENT_ENABLED", "true")
    for key in [
        "APP_ENV",
        "WEBCALL_AI_TTS_RUNTIME_ENABLED",
        "WEBCALL_AI_TTS_RUNTIME_MODE",
        "WEBCALL_AI_VOICE_EGRESS_ENABLED",
        "WEBCALL_AI_VOICE_EGRESS_MODE",
        "WEBCALL_AI_VOICE_EGRESS_SMOKE_ENABLED",
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


def test_worker_default_output_unchanged(db):
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


def test_worker_tts_runtime_fake_egress_returns_counters_and_writes_turn_action(db, monkeypatch):
    _voice_session(db)
    monkeypatch.setenv("WEBCALL_AI_TTS_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_VOICE_EGRESS_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_VOICE_EGRESS_MODE", "fake_audio_reference")
    get_webcall_ai_settings.cache_clear()

    result = run_webcall_ai_worker_once(db, "worker-a", limit=10, lease_seconds=30)

    assert result["released"] == 1
    assert result["failed"] == 0
    assert result["tts_events"] == 1
    assert result["tts_runtime_events"] == 1
    assert result["voice_egress_sent"] == 1
    assert result["voice_egress_failures"] == 0
    assert db.query(WebchatVoiceAITurn).count() == 1
    assert db.query(WebchatVoiceAIAction).count() == 1


def test_worker_tts_runtime_failure_does_not_publish_egress_or_write_rows(db, monkeypatch):
    session = _voice_session(db)
    monkeypatch.setenv("WEBCALL_AI_TTS_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_VOICE_EGRESS_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()
    monkeypatch.setattr(
        "app.services.webcall_ai.tts_runtime.get_tts_provider",
        lambda settings: FailingTTSProvider(),
    )
    monkeypatch.setattr(
        "app.services.webcall_ai.mock_turn_executor.get_webcall_voice_egress_client",
        lambda settings: (_ for _ in ()).throw(AssertionError("egress should not be called")),
    )

    result = run_webcall_ai_worker_once(db, "worker-a", limit=10, lease_seconds=30)
    db.refresh(session)

    assert result["released"] == 0
    assert result["failed"] == 1
    assert result["turns"] == 0
    assert result["tts_events"] == 0
    assert result["tts_runtime_events"] == 0
    assert result["voice_egress_sent"] == 0
    assert result["voice_egress_failures"] == 0
    assert db.query(WebchatVoiceAITurn).count() == 0
    assert db.query(WebchatVoiceAIAction).count() == 0
    assert session.ai_agent_status == "failed"


def test_worker_voice_egress_stub_failure_counts_failure_and_rolls_back_rows(db, monkeypatch):
    session = _voice_session(db)
    monkeypatch.setenv("WEBCALL_AI_TTS_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_VOICE_EGRESS_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_VOICE_EGRESS_MODE", "livekit_audio_publish_stub")
    get_webcall_ai_settings.cache_clear()

    result = run_webcall_ai_worker_once(db, "worker-a", limit=10, lease_seconds=30)
    db.refresh(session)

    assert result["released"] == 0
    assert result["failed"] == 1
    assert result["turns"] == 0
    assert result["tts_events"] == 0
    assert result["tts_runtime_events"] == 1
    assert result["voice_egress_sent"] == 0
    assert result["voice_egress_failures"] == 1
    assert db.query(WebchatVoiceAITurn).count() == 0
    assert db.query(WebchatVoiceAIAction).count() == 0
    assert session.ai_agent_status == "failed"
