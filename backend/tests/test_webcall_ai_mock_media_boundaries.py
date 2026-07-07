import os
from dataclasses import asdict
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webcall_ai_mock_media_tests.db")

import pytest

from app import models, operator_models, tool_models, voice_models, webchat_models  # noqa: F401,E402
from app.db import Base, SessionLocal, engine
from app.services.webcall_ai.config import get_webcall_ai_settings
from app.services.webcall_ai.lifecycle import WEBCALL_AI_STATUS_CLAIMED, WEBCALL_AI_STATUS_RELEASED
from app.services.webcall_ai.media_schemas import MockSTTInput, MockTTSInput
from app.services.webcall_ai.mock_media_provider import (
    MOCK_CUSTOMER_TEXT,
    MOCK_TTS_AUDIO_REFERENCE,
    MOCK_TTS_SYNTHESIS_STATUS,
    MockSTTProvider,
    MockTTSProvider,
)
from app.services.webcall_ai.mock_turn_executor import (
    MOCK_ACTION,
    MOCK_AI_RESPONSE,
    MOCK_DECISION_REASON,
    MOCK_RESULT_STATUS,
    execute_mock_turn_for_claimed_session,
)
from app.services.webcall_ai.worker import run_webcall_ai_worker_once
from app.utils.time import utc_now
from app.voice_models import WebchatVoiceAIAction, WebchatVoiceAITurn, WebchatVoiceSession


@pytest.fixture(autouse=True)
def clean_db_and_env(monkeypatch):
    get_webcall_ai_settings.cache_clear()
    monkeypatch.setenv("WEBCALL_AI_AGENT_ENABLED", "true")
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


def _voice_session(db, *, ai_agent_status: str | None = None, worker_id: str | None = None) -> WebchatVoiceSession:
    now = utc_now()
    session = WebchatVoiceSession(
        public_id=f"voice_{uuid4().hex}",
        conversation_id=1,
        ticket_id=1,
        provider="livekit",
        provider_room_name=f"room_{uuid4().hex}",
        status="ringing",
        ai_agent_status=ai_agent_status,
        ai_agent_worker_id=worker_id,
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def test_mock_stt_provider_returns_deterministic_final_text():
    result = MockSTTProvider().transcribe(MockSTTInput(voice_session_id=123, worker_id="worker-a"))

    assert asdict(result) == {
        "text_redacted": MOCK_CUSTOMER_TEXT,
        "language": "en",
        "confidence": 100,
        "is_final": True,
        "provider": "mock",
        "event_count": 1,
        "status": "ok",
        "error_code": None,
    }


def test_mock_stt_boundary_does_not_accept_raw_audio_bytes():
    fields = MockSTTInput.__dataclass_fields__

    assert set(fields) == {"voice_session_id", "worker_id", "locale", "audio_reference"}
    assert "audio" not in fields
    assert "audio_bytes" not in fields


def test_mock_tts_provider_returns_safe_metadata_only():
    result = MockTTSProvider().synthesize(
        MockTTSInput(
            voice_session_id=123,
            worker_id="worker-a",
            text_redacted=MOCK_AI_RESPONSE,
            language="en",
        )
    )

    assert asdict(result) == {
        "provider": "mock",
        "voice": "mock_support_voice",
        "language": "en",
        "text_redacted": MOCK_AI_RESPONSE,
        "synthesis_status": MOCK_TTS_SYNTHESIS_STATUS,
        "audio_reference": MOCK_TTS_AUDIO_REFERENCE,
        "event_count": 1,
        "error_code": None,
    }
    assert not hasattr(result, "audio_bytes")
    assert not hasattr(result, "audio_base64")


def test_mock_turn_executor_writes_stt_text_ai_reply_and_safe_action(db):
    session = _voice_session(db, ai_agent_status=WEBCALL_AI_STATUS_CLAIMED, worker_id="worker-a")

    result = execute_mock_turn_for_claimed_session(db, session=session, worker_id="worker-a")
    turn = result.turn
    action = db.query(WebchatVoiceAIAction).filter(WebchatVoiceAIAction.turn_id == turn.id).one()

    assert result.stt_events == 1
    assert result.tts_events == 1
    assert turn.customer_text_redacted == MOCK_CUSTOMER_TEXT
    assert turn.ai_response_text_redacted == MOCK_AI_RESPONSE
    assert turn.provider == "mock"
    assert turn.stt_provider == "mock"
    assert turn.tts_provider == "mock"
    assert turn.tracking_number_hash is None
    assert action.model_action == MOCK_ACTION
    assert action.nexus_decision == "allowed"
    assert action.decision_reason == MOCK_DECISION_REASON
    assert action.speedaf_tool_name is None
    assert action.background_job_id is None
    assert action.tool_call_log_id is None
    assert action.result_status == MOCK_RESULT_STATUS


def test_worker_once_returns_media_counters_and_releases(db):
    session = _voice_session(db)

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
    db.refresh(session)
    assert session.ai_agent_status == WEBCALL_AI_STATUS_RELEASED
    assert session.ai_handoff_reason == "pr4_mock_media_turn_complete"
    assert db.query(WebchatVoiceAITurn).count() == 1
    assert db.query(WebchatVoiceAIAction).count() == 1


def test_disabled_worker_returns_zero_media_counters_and_writes_no_rows(db, monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_AGENT_ENABLED", "false")
    get_webcall_ai_settings.cache_clear()
    _voice_session(db)

    result = run_webcall_ai_worker_once(db, "worker-a", limit=10, lease_seconds=30)

    assert result == {
        "claimed": 0,
        "released": 0,
        "failed": 0,
        "skipped": 1,
        "turns": 0,
        "stt_events": 0,
        "tts_events": 0,
    }
    assert db.query(WebchatVoiceAITurn).count() == 0
    assert db.query(WebchatVoiceAIAction).count() == 0


def test_released_session_is_not_reprocessed(db):
    _voice_session(db)

    first = run_webcall_ai_worker_once(db, "worker-a")
    second = run_webcall_ai_worker_once(db, "worker-a")

    assert first["turns"] == 1
    assert first["stt_events"] == 1
    assert first["tts_events"] == 1
    assert second["turns"] == 0
    assert second["stt_events"] == 0
    assert second["tts_events"] == 0
    assert db.query(WebchatVoiceAITurn).count() == 1
    assert db.query(WebchatVoiceAIAction).count() == 1


def test_failure_path_marks_session_failed_and_zeroes_media_counters(db, monkeypatch):
    session = _voice_session(db)

    def fail_turn(*args, **kwargs):
        raise RuntimeError("simulated")

    monkeypatch.setattr("app.services.webcall_ai.worker.execute_mock_turn_for_claimed_session", fail_turn)
    result = run_webcall_ai_worker_once(db, "worker-a", limit=10, lease_seconds=30)
    db.refresh(session)

    assert result["claimed"] == 1
    assert result["failed"] == 1
    assert result["turns"] == 0
    assert result["stt_events"] == 0
    assert result["tts_events"] == 0
    assert session.ai_agent_status == "failed"
    assert session.ai_agent_error_code == "mock_turn_failed"
