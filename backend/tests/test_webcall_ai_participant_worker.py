import os
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webcall_ai_participant_worker_tests.db")

import pytest

from app import models, operator_models, tool_models, voice_models, webchat_models  # noqa: F401,E402
from app.db import Base, SessionLocal, engine
from app.services.voice_provider import VoiceParticipantToken
from app.services.webcall_ai.config import get_webcall_ai_settings
from app.services.webcall_ai.participant_service import ai_participant_identity
from app.services.webcall_ai.room_client import WebCallAIRoomJoinResult
from app.services.webcall_ai.worker import run_webcall_ai_worker_once
from app.utils.time import utc_now
from app.voice_models import WebchatVoiceAIAction, WebchatVoiceAITurn, WebchatVoiceParticipant, WebchatVoiceSession


class FailingJoinRoomClient:
    def issue_ai_token(self, *, session, participant_identity: str, ttl_seconds: int) -> VoiceParticipantToken:
        return VoiceParticipantToken(
            provider="fake_room_client",
            room_name=session.provider_room_name,
            participant_identity=participant_identity,
            participant_token="secret-failing-token",
            expires_in_seconds=ttl_seconds,
        )

    def join(self, *, session, participant_identity: str, token: VoiceParticipantToken) -> WebCallAIRoomJoinResult:
        return WebCallAIRoomJoinResult(
            joined=False,
            provider="fake_room_client",
            participant_identity=participant_identity,
            status="failed",
            error_code="fake_join_failed",
        )

    def leave(self, *, session, participant_identity: str):
        raise AssertionError("leave should not be called after failed setup")


@pytest.fixture(autouse=True)
def clean_db_and_env(monkeypatch):
    get_webcall_ai_settings.cache_clear()
    monkeypatch.setenv("WEBCALL_AI_AGENT_ENABLED", "true")
    for key in [
        "APP_ENV",
        "WEBCALL_AI_PARTICIPANT_ENABLED",
        "WEBCALL_AI_PARTICIPANT_MODE",
        "WEBCALL_AI_PARTICIPANT_TOKEN_TTL_SECONDS",
        "WEBCALL_AI_PARTICIPANT_ID_PREFIX",
        "WEBCALL_AI_LIVEKIT_TOKEN_ISSUER_ENABLED",
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


def test_participant_enabled_worker_creates_joins_leaves_and_releases(db, monkeypatch):
    session = _voice_session(db)
    monkeypatch.setenv("WEBCALL_AI_PARTICIPANT_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()

    result = run_webcall_ai_worker_once(db, "worker-a", limit=10, lease_seconds=30)
    db.refresh(session)
    participant = db.query(WebchatVoiceParticipant).one()

    assert result["claimed"] == 1
    assert result["released"] == 1
    assert result["failed"] == 0
    assert result["turns"] == 1
    assert result["stt_events"] == 1
    assert result["tts_events"] == 1
    assert result["participants"] == 1
    assert result["participant_joins"] == 1
    assert result["participant_leaves"] == 1
    assert session.ai_agent_status == "released"
    assert participant.participant_type == "ai"
    assert participant.provider_identity == ai_participant_identity(session)
    assert participant.status == "left"
    assert participant.joined_at is not None
    assert participant.left_at is not None
    assert db.query(WebchatVoiceAITurn).count() == 1
    assert db.query(WebchatVoiceAIAction).count() == 1


def test_participant_setup_failure_marks_failed_and_writes_no_turn_or_action(db, monkeypatch):
    session = _voice_session(db)
    monkeypatch.setenv("WEBCALL_AI_PARTICIPANT_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()
    monkeypatch.setattr(
        "app.services.webcall_ai.worker.get_webcall_ai_room_client",
        lambda settings: FailingJoinRoomClient(),
    )

    result = run_webcall_ai_worker_once(db, "worker-a", limit=10, lease_seconds=30)
    db.refresh(session)

    assert result["claimed"] == 1
    assert result["released"] == 0
    assert result["failed"] == 1
    assert result["turns"] == 0
    assert result["stt_events"] == 0
    assert result["tts_events"] == 0
    assert result["participants"] == 0
    assert result["participant_joins"] == 0
    assert result["participant_leaves"] == 0
    assert session.ai_agent_status == "failed"
    assert session.ai_agent_error_code == "mock_turn_failed"
    assert db.query(WebchatVoiceParticipant).count() == 0
    assert db.query(WebchatVoiceAITurn).count() == 0
    assert db.query(WebchatVoiceAIAction).count() == 0
