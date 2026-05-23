import os
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webcall_ai_room_client_tests.db")

import pytest

from app import models, operator_models, tool_models, voice_models, webchat_fast_models, webchat_models  # noqa: F401,E402
from app.db import Base, SessionLocal, engine
from app.services.webcall_ai.config import get_webcall_ai_settings
from app.services.webcall_ai.participant_service import ai_participant_identity
from app.services.webcall_ai.room_client import FakeWebCallAIRoomClient, get_webcall_ai_room_client
from app.utils.time import utc_now
from app.voice_models import WebchatVoiceSession


@pytest.fixture(autouse=True)
def clean_db_and_env(monkeypatch):
    get_webcall_ai_settings.cache_clear()
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


def test_fake_room_client_issue_join_leave_returns_safe_results(db):
    session = _voice_session(db)
    identity = ai_participant_identity(session)
    client = FakeWebCallAIRoomClient()

    token = client.issue_ai_token(session=session, participant_identity=identity, ttl_seconds=300)
    join_result = client.join(session=session, participant_identity=identity, token=token)
    leave_result = client.leave(session=session, participant_identity=identity)

    assert token.provider == "fake_room_client"
    assert token.room_name == session.provider_room_name
    assert token.participant_identity == identity
    assert token.participant_token.startswith("fake_ai_participant_token_")
    assert token.expires_in_seconds == 300
    assert join_result.joined is True
    assert join_result.provider == "fake_room_client"
    assert leave_result.left is True
    assert leave_result.provider == "fake_room_client"


def test_room_client_router_returns_fake_client_by_default():
    assert isinstance(get_webcall_ai_room_client(), FakeWebCallAIRoomClient)
