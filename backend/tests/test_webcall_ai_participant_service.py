import os
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webcall_ai_participant_service_tests.db")

import pytest

from app import models, operator_models, tool_models, voice_models, webchat_models  # noqa: F401,E402
from app.db import Base, SessionLocal, engine
from app.services.voice_provider import VoiceParticipantToken
from app.services.webcall_ai.config import get_webcall_ai_settings
from app.services.webcall_ai.participant_service import (
    AI_PARTICIPANT_LABEL,
    ai_participant_identity,
    ensure_ai_participant_record,
    mark_ai_participant_joined,
    mark_ai_participant_left,
)
from app.utils.time import utc_now
from app.voice_models import WebchatVoiceParticipant, WebchatVoiceSession


@pytest.fixture(autouse=True)
def clean_db_and_env(monkeypatch):
    get_webcall_ai_settings.cache_clear()
    for key in [
        "APP_ENV",
        "WEBCALL_AI_PARTICIPANT_ENABLED",
        "WEBCALL_AI_PARTICIPANT_MODE",
        "WEBCALL_AI_PARTICIPANT_TOKEN_TTL_SECONDS",
        "WEBCALL_AI_PARTICIPANT_ID_PREFIX",
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


def _voice_session(db, *, public_id: str | None = None) -> WebchatVoiceSession:
    now = utc_now()
    session = WebchatVoiceSession(
        public_id=public_id or f"voice_{uuid4().hex}",
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


def test_ai_participant_identity_is_deterministic_and_safe(db, monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_PARTICIPANT_ID_PREFIX", "ai webcall!")
    get_webcall_ai_settings.cache_clear()
    session = _voice_session(db, public_id="voice unsafe/id with spaces")

    first = ai_participant_identity(session)
    second = ai_participant_identity(session)

    assert first == second
    assert first == "ai_webcall_voice_unsafe_id_with_spaces"
    assert len(first) <= 160
    assert " " not in first
    assert "/" not in first


def test_ensure_ai_participant_record_creates_one_ai_record(db):
    session = _voice_session(db)

    participant = ensure_ai_participant_record(db, session=session, worker_id="worker-a")

    assert participant.participant_type == "ai"
    assert participant.user_id is None
    assert participant.visitor_label == AI_PARTICIPANT_LABEL
    assert participant.provider_identity == ai_participant_identity(session)
    assert participant.status == "invited"
    assert db.query(WebchatVoiceParticipant).count() == 1


def test_ensure_ai_participant_record_is_idempotent(db):
    session = _voice_session(db)

    first = ensure_ai_participant_record(db, session=session, worker_id="worker-a")
    second = ensure_ai_participant_record(db, session=session, worker_id="worker-a")

    assert first.id == second.id
    assert db.query(WebchatVoiceParticipant).count() == 1


def test_participant_token_is_not_persisted(db):
    session = _voice_session(db)
    token = VoiceParticipantToken(
        provider="fake_room_client",
        room_name=session.provider_room_name,
        participant_identity=ai_participant_identity(session),
        participant_token="secret-ai-token-value",
        expires_in_seconds=300,
    )

    participant = ensure_ai_participant_record(db, session=session, worker_id="worker-a", token=token)
    db.commit()
    db.refresh(participant)

    values = [str(value) for value in participant.__dict__.values() if value is not None]
    assert participant.status == "token_issued"
    assert "secret-ai-token-value" not in values


def test_mark_joined_and_left_updates_safe_statuses(db):
    session = _voice_session(db)
    participant = ensure_ai_participant_record(db, session=session, worker_id="worker-a")

    assert mark_ai_participant_joined(db, session=session, worker_id="worker-a") is True
    assert mark_ai_participant_left(db, session=session, worker_id="worker-a", reason="done") is True
    db.refresh(participant)

    assert participant.status == "left"
    assert participant.joined_at is not None
    assert participant.left_at is not None
