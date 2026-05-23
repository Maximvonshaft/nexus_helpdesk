import os
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webcall_ai_pilot_session_tests.db")

import pytest

from app import models, operator_models, tool_models, voice_models, webchat_fast_models, webchat_models  # noqa: F401,E402
from app.db import Base, SessionLocal, engine
from app.services.webcall_ai.config import get_webcall_ai_settings
from app.services.webcall_ai.pilot_session_source import resolve_or_create_pilot_voice_session
from app.utils.time import utc_now
from app.voice_models import WebchatVoiceSession


@pytest.fixture(autouse=True)
def clean_db_and_env(monkeypatch):
    get_webcall_ai_settings.cache_clear()
    for key in [
        "APP_ENV",
        "WEBCALL_AI_PILOT_FIXTURE_ENABLED",
        "WEBCALL_AI_PILOT_FIXTURE_ALLOW_DB_WRITE",
        "WEBCALL_AI_PILOT_SESSION_PUBLIC_ID",
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


def _voice_session(db, *, public_id: str, status: str = "ringing", accepted_by_user_id: int | None = None):
    now = utc_now()
    session = WebchatVoiceSession(
        public_id=public_id,
        conversation_id=1,
        ticket_id=1,
        provider="livekit",
        provider_room_name=f"room_{uuid4().hex}",
        status=status,
        accepted_by_user_id=accepted_by_user_id,
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def test_existing_session_selected_by_public_id_when_claimable(db, monkeypatch):
    session = _voice_session(db, public_id="voice_allowed")
    monkeypatch.setenv("WEBCALL_AI_PILOT_SESSION_PUBLIC_ID", session.public_id)
    get_webcall_ai_settings.cache_clear()

    resolved = resolve_or_create_pilot_voice_session(
        db,
        settings=get_webcall_ai_settings(),
        mode="simulated_full_loop",
    )

    assert resolved.id == session.id


def test_non_claimable_existing_session_is_rejected(db, monkeypatch):
    session = _voice_session(db, public_id="voice_busy", accepted_by_user_id=123)
    monkeypatch.setenv("WEBCALL_AI_PILOT_SESSION_PUBLIC_ID", session.public_id)
    get_webcall_ai_settings.cache_clear()

    assert resolve_or_create_pilot_voice_session(db, settings=get_webcall_ai_settings(), mode="simulated_full_loop") is None


def test_fixture_creation_requires_both_fixture_flags(db, monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_PILOT_FIXTURE_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()

    assert resolve_or_create_pilot_voice_session(db, settings=get_webcall_ai_settings(), mode="simulated_full_loop") is None

    monkeypatch.setenv("WEBCALL_AI_PILOT_FIXTURE_ALLOW_DB_WRITE", "true")
    get_webcall_ai_settings.cache_clear()

    session = resolve_or_create_pilot_voice_session(db, settings=get_webcall_ai_settings(), mode="simulated_full_loop")
    assert session is not None
    assert session.public_id.startswith("pilot_fixture_")
    assert session.provider == "livekit"


def test_fixture_creation_blocked_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("WEBCALL_AI_PILOT_FIXTURE_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()

    with pytest.raises(RuntimeError, match="WEBCALL_AI_PILOT_FIXTURE_ENABLED"):
        get_webcall_ai_settings()
