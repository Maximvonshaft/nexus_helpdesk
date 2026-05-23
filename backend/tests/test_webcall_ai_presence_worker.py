import os
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webcall_ai_presence_worker_tests.db")

import pytest

from app import models, operator_models, tool_models, voice_models, webchat_fast_models, webchat_models  # noqa: F401,E402
from app.db import Base, SessionLocal, engine
from app.services.voice_provider import VoiceParticipantToken
from app.services.webcall_ai.config import get_webcall_ai_settings
from app.services.webcall_ai.presence_client import WebCallAIPresenceJoinResult
from app.services.webcall_ai.worker import run_webcall_ai_worker_once
from app.utils.time import utc_now
from app.voice_models import WebchatVoiceAIAction, WebchatVoiceAITurn, WebchatVoiceParticipant, WebchatVoiceSession


class FailingPresenceClient:
    def join_no_media(self, *, session, participant_identity: str, token: VoiceParticipantToken, timeout_ms: int):
        return WebCallAIPresenceJoinResult(
            joined=False,
            provider="fake_no_media",
            participant_identity=participant_identity,
            status="failed",
            error_code="fake_presence_join_failed",
        )

    def leave(self, *, session, participant_identity: str):
        raise AssertionError("leave should not be called after failed presence join")


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
        "WEBCALL_AI_ROOM_PRESENCE_ENABLED",
        "WEBCALL_AI_ROOM_PRESENCE_MODE",
        "WEBCALL_AI_ROOM_PRESENCE_JOIN_TIMEOUT_MS",
        "WEBCALL_AI_ROOM_PRESENCE_SMOKE_ENABLED",
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


def test_worker_fake_no_media_presence_joins_leaves_and_writes_mock_turn(db, monkeypatch):
    session = _voice_session(db)
    monkeypatch.setenv("WEBCALL_AI_ROOM_PRESENCE_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()

    result = run_webcall_ai_worker_once(db, "worker-a", limit=10, lease_seconds=30)
    db.refresh(session)
    participant = db.query(WebchatVoiceParticipant).one()

    assert result["claimed"] == 1
    assert result["released"] == 1
    assert result["failed"] == 0
    assert result["turns"] == 1
    assert result["presence_joins"] == 1
    assert result["presence_leaves"] == 1
    assert result["presence_failures"] == 0
    assert "participants" not in result
    assert session.ai_agent_status == "released"
    assert participant.status == "left"
    assert db.query(WebchatVoiceAITurn).count() == 1
    assert db.query(WebchatVoiceAIAction).count() == 1


def test_presence_join_failure_marks_failed_and_writes_no_turn_or_action(db, monkeypatch):
    session = _voice_session(db)
    monkeypatch.setenv("WEBCALL_AI_ROOM_PRESENCE_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()
    monkeypatch.setattr(
        "app.services.webcall_ai.worker.get_webcall_ai_presence_client",
        lambda settings: FailingPresenceClient(),
    )

    result = run_webcall_ai_worker_once(db, "worker-a", limit=10, lease_seconds=30)
    db.refresh(session)

    assert result["claimed"] == 1
    assert result["released"] == 0
    assert result["failed"] == 1
    assert result["turns"] == 0
    assert result["presence_joins"] == 0
    assert result["presence_leaves"] == 0
    assert result["presence_failures"] == 1
    assert session.ai_agent_status == "failed"
    assert db.query(WebchatVoiceParticipant).count() == 0
    assert db.query(WebchatVoiceAITurn).count() == 0
    assert db.query(WebchatVoiceAIAction).count() == 0
