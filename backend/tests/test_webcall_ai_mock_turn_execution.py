import os
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webcall_ai_mock_turn_tests.db")

import pytest

from app import models, operator_models, tool_models, voice_models, webchat_models  # noqa: F401,E402
from app.db import Base, SessionLocal, engine
from app.services.webcall_ai.config import get_webcall_ai_settings
from app.services.webcall_ai.lifecycle import (
    WEBCALL_AI_STATUS_CLAIMED,
    WEBCALL_AI_STATUS_RELEASED,
    claim_webcall_ai_sessions,
)
from app.services.webcall_ai.mock_media_provider import MOCK_CUSTOMER_TEXT
from app.services.webcall_ai.mock_turn_executor import (
    MOCK_ACTION,
    MOCK_AI_RESPONSE,
    MOCK_DECISION_REASON,
    MOCK_INTENT,
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


def test_mock_turn_requires_claimed_session_and_owner(db):
    session = _voice_session(db)

    with pytest.raises(ValueError, match="claimed"):
        execute_mock_turn_for_claimed_session(db, session=session, worker_id="worker-a")
    assert db.query(WebchatVoiceAITurn).count() == 0
    assert db.query(WebchatVoiceAIAction).count() == 0

    session.ai_agent_status = WEBCALL_AI_STATUS_CLAIMED
    session.ai_agent_worker_id = "worker-a"
    db.commit()

    with pytest.raises(ValueError, match="claimed"):
        execute_mock_turn_for_claimed_session(db, session=session, worker_id="worker-b")
    assert db.query(WebchatVoiceAITurn).count() == 0
    assert db.query(WebchatVoiceAIAction).count() == 0


def test_mock_turn_writes_turn_and_action(db):
    session = _voice_session(db, ai_agent_status=WEBCALL_AI_STATUS_CLAIMED, worker_id="worker-a")

    result = execute_mock_turn_for_claimed_session(db, session=session, worker_id="worker-a")
    turn = result.turn
    action = db.query(WebchatVoiceAIAction).filter(WebchatVoiceAIAction.turn_id == turn.id).one()
    db.refresh(session)

    assert result.stt_events == 1
    assert result.tts_events == 1
    assert turn.turn_index == 1
    assert turn.customer_text_redacted == MOCK_CUSTOMER_TEXT
    assert turn.ai_response_text_redacted == MOCK_AI_RESPONSE
    assert turn.language == "en"
    assert turn.intent == MOCK_INTENT
    assert turn.action == MOCK_ACTION
    assert turn.tracking_number_hash is None
    assert turn.handoff_required is False
    assert turn.handoff_reason is None
    assert turn.confidence == 100
    assert turn.provider == "mock"
    assert turn.stt_provider == "mock"
    assert turn.tts_provider == "mock"
    assert turn.latency_ms == 0
    assert action.model_action == MOCK_ACTION
    assert action.nexus_decision == "allowed"
    assert action.decision_reason == MOCK_DECISION_REASON
    assert action.speedaf_tool_name is None
    assert action.background_job_id is None
    assert action.tool_call_log_id is None
    assert action.result_status == MOCK_RESULT_STATUS
    assert session.ai_turn_count == 1
    assert session.ai_language == "en"


def test_worker_once_claims_executes_mock_turn_and_releases(db):
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


def test_worker_once_does_not_execute_when_disabled(db, monkeypatch):
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
    session = _voice_session(db)

    assert run_webcall_ai_worker_once(db, "worker-a")["turns"] == 1
    assert run_webcall_ai_worker_once(db, "worker-a")["turns"] == 0
    db.refresh(session)
    assert session.ai_agent_status == WEBCALL_AI_STATUS_RELEASED
    assert db.query(WebchatVoiceAITurn).count() == 1
    assert db.query(WebchatVoiceAIAction).count() == 1


def test_mock_turn_no_speedaf_or_external_tool_fields(db):
    session = _voice_session(db, ai_agent_status=WEBCALL_AI_STATUS_CLAIMED, worker_id="worker-a")

    turn = execute_mock_turn_for_claimed_session(db, session=session, worker_id="worker-a").turn
    action = db.query(WebchatVoiceAIAction).filter(WebchatVoiceAIAction.turn_id == turn.id).one()

    assert action.speedaf_tool_name is None
    assert action.background_job_id is None
    assert action.tool_call_log_id is None
    assert turn.customer_text_redacted == MOCK_CUSTOMER_TEXT
    assert turn.tracking_number_hash is None


def test_mock_turn_uses_next_turn_index(db):
    session = _voice_session(db, ai_agent_status=WEBCALL_AI_STATUS_CLAIMED, worker_id="worker-a")
    session.ai_turn_count = 1
    db.commit()

    turn = execute_mock_turn_for_claimed_session(db, session=session, worker_id="worker-a").turn
    db.refresh(session)

    assert turn.turn_index == 2
    assert session.ai_turn_count == 2


def test_claimed_session_failure_marks_failed_when_mock_turn_raises(db, monkeypatch):
    session = _voice_session(db)

    def fail_turn(*args, **kwargs):
        raise RuntimeError("simulated")

    monkeypatch.setattr("app.services.webcall_ai.worker.execute_mock_turn_for_claimed_session", fail_turn)
    result = run_webcall_ai_worker_once(db, "worker-a", limit=10, lease_seconds=30)
    db.refresh(session)

    assert result["failed"] == 1
    assert result["turns"] == 0
    assert result["stt_events"] == 0
    assert result["tts_events"] == 0
    assert session.ai_agent_status == "failed"
    assert session.ai_agent_error_code == "mock_turn_failed"
    assert session.ai_agent_lease_expires_at is None
