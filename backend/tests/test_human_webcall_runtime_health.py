from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Ticket, User  # noqa: F401 - registers referenced tables
from app.services.provider_runtime_status import get_human_webcall_runtime_status
from app.utils.time import utc_now
from app.voice_models import WebchatVoiceSession
from app.webchat_models import WebchatConversation, WebchatEvent, WebchatMessage  # noqa: F401 - registers referenced tables


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.fixture(autouse=True)
def voice_env(monkeypatch):
    for key in [
        "WEBCHAT_VOICE_ENABLED",
        "WEBCHAT_HUMAN_CALL_ENABLED",
        "WEBCHAT_LIVE_AI_VOICE_ENABLED",
        "WEBCHAT_VOICE_PROVIDER",
        "WEBCHAT_VOICE_RECORDING_ENABLED",
        "WEBCHAT_VOICE_TRANSCRIPTION_ENABLED",
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
        "WEBCHAT_VOICE_CONNECT_SRC",
    ]:
        monkeypatch.delenv(key, raising=False)


def _session(public_id: str, status: str, expires_delta: timedelta) -> WebchatVoiceSession:
    now = utc_now()
    return WebchatVoiceSession(
        public_id=public_id,
        conversation_id=1,
        ticket_id=1,
        provider="mock",
        provider_room_name=f"webchat_{public_id}",
        status=status,
        expires_at=now + expires_delta,
        created_at=now,
        updated_at=now,
    )


def test_human_webcall_runtime_health_reports_stale_counts_and_disabled_verdict(db_session):
    db_session.add_all(
        [
            _session("wv_active_stale", "active", timedelta(minutes=-10)),
            _session("wv_accepted_stale", "accepted", timedelta(minutes=-10)),
            _session("wv_ringing_stale", "ringing", timedelta(minutes=-10)),
            _session("wv_created_fresh", "created", timedelta(minutes=10)),
            _session("wv_ended", "ended", timedelta(minutes=-10)),
        ]
    )
    db_session.flush()

    status = get_human_webcall_runtime_status(db_session)

    assert status["webchat_voice_enabled"] is False
    assert status["provider"] == "mock"
    assert status["recording_enabled"] is False
    assert status["transcription_enabled"] is False
    assert status["active_session_count"] == 1
    assert status["ringing_session_count"] == 2
    assert status["stale_active_session_count"] == 2
    assert status["stale_ringing_session_count"] == 1
    assert status["readiness_verdict"] == "disabled"
    assert status["warnings"] == []


def test_human_webcall_runtime_health_ready_when_voice_enabled(monkeypatch, db_session):
    monkeypatch.setenv("WEBCHAT_VOICE_ENABLED", "false")
    monkeypatch.setenv("WEBCHAT_HUMAN_CALL_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_VOICE_PROVIDER", "mock")

    status = get_human_webcall_runtime_status(db_session)

    assert status["webchat_voice_enabled"] is True
    assert status["provider"] == "mock"
    assert status["readiness_verdict"] == "ready"
