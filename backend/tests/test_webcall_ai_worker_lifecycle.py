import os
from datetime import timedelta
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webcall_ai_worker_lifecycle_tests.db")

import pytest

from app import models, operator_models, tool_models, voice_models, webchat_fast_models, webchat_models  # noqa: F401,E402
from app.db import Base, SessionLocal, engine
from app.services.webcall_ai.config import get_webcall_ai_settings
from app.services.webcall_ai.lifecycle import (
    WEBCALL_AI_STATUS_CLAIMED,
    WEBCALL_AI_STATUS_FAILED,
    WEBCALL_AI_STATUS_PENDING,
    WEBCALL_AI_STATUS_RELEASED,
    claim_webcall_ai_sessions,
    fail_webcall_ai_session,
    heartbeat_webcall_ai_session,
    release_webcall_ai_session,
)
from app.services.webcall_ai.worker import run_webcall_ai_worker_once
from app.utils.time import utc_now
from app.voice_models import WebchatVoiceSession


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


def _voice_session(
    db,
    *,
    provider: str = "livekit",
    status: str = "ringing",
    ai_agent_status: str | None = None,
    accepted_by_user_id: int | None = None,
    ended: bool = False,
    expired: bool = False,
    lease_expired: bool = False,
) -> WebchatVoiceSession:
    now = utc_now()
    session = WebchatVoiceSession(
        public_id=f"voice_{uuid4().hex}",
        conversation_id=1,
        ticket_id=1,
        provider=provider,
        provider_room_name=f"room_{uuid4().hex}",
        status=status,
        ai_agent_status=ai_agent_status,
        ai_agent_worker_id="old-worker" if ai_agent_status == WEBCALL_AI_STATUS_CLAIMED else None,
        ai_agent_lease_expires_at=now - timedelta(seconds=1) if lease_expired else None,
        accepted_by_user_id=accepted_by_user_id,
        ended_at=now if ended else None,
        expires_at=now - timedelta(seconds=1) if expired else None,
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def test_config_disabled_means_no_claims(db, monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_AGENT_ENABLED", "false")
    get_webcall_ai_settings.cache_clear()
    _voice_session(db)

    assert claim_webcall_ai_sessions(db, "worker-a") == []


def test_livekit_ringing_session_is_claimable_when_enabled(db):
    session = _voice_session(db)

    claimed = claim_webcall_ai_sessions(db, "worker-a", lease_seconds=45)

    assert [item.id for item in claimed] == [session.id]
    db.refresh(session)
    assert session.ai_agent_status == WEBCALL_AI_STATUS_CLAIMED
    assert session.ai_agent_worker_id == "worker-a"
    assert session.ai_agent_claimed_at is not None
    assert session.ai_agent_last_heartbeat_at is not None
    assert session.ai_agent_lease_expires_at is not None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"provider": "mock"},
        {"ended": True},
        {"accepted_by_user_id": 123},
        {"expired": True},
        {"status": "active"},
        {"ai_agent_status": WEBCALL_AI_STATUS_RELEASED},
    ],
)
def test_ineligible_sessions_are_not_claimed(db, kwargs):
    _voice_session(db, **kwargs)

    assert claim_webcall_ai_sessions(db, "worker-a") == []


def test_pending_session_is_claimable(db):
    session = _voice_session(db, ai_agent_status=WEBCALL_AI_STATUS_PENDING)

    claimed = claim_webcall_ai_sessions(db, "worker-a")

    assert [item.id for item in claimed] == [session.id]


def test_heartbeat_extends_lease_for_same_worker_only(db):
    session = _voice_session(db)
    claim_webcall_ai_sessions(db, "worker-a", lease_seconds=30)
    db.refresh(session)
    original_lease = session.ai_agent_lease_expires_at

    assert heartbeat_webcall_ai_session(db, session.id, "worker-b", lease_seconds=120) is False
    assert heartbeat_webcall_ai_session(db, session.id, "worker-a", lease_seconds=120) is True
    db.refresh(session)
    assert session.ai_agent_lease_expires_at > original_lease


def test_release_requires_same_worker_id(db):
    session = _voice_session(db)
    claim_webcall_ai_sessions(db, "worker-a")

    assert release_webcall_ai_session(db, session.id, "worker-b") is False
    assert release_webcall_ai_session(db, session.id, "worker-a", reason="done") is True
    db.refresh(session)
    assert session.ai_agent_status == WEBCALL_AI_STATUS_RELEASED
    assert session.ai_handoff_reason == "done"
    assert session.ai_agent_lease_expires_at is None


def test_fail_requires_same_worker_id_and_records_error(db):
    session = _voice_session(db)
    claim_webcall_ai_sessions(db, "worker-a")

    assert fail_webcall_ai_session(db, session.id, "worker-b", "wrong") is False
    assert fail_webcall_ai_session(db, session.id, "worker-a", "boom", "failure detail") is True
    db.refresh(session)
    assert session.ai_agent_status == WEBCALL_AI_STATUS_FAILED
    assert session.ai_agent_error_code == "boom"
    assert session.ai_agent_error_message == "failure detail"
    assert session.ai_agent_lease_expires_at is None


def test_duplicate_claim_does_not_claim_same_session_twice(db):
    session = _voice_session(db)

    first = claim_webcall_ai_sessions(db, "worker-a")
    second = claim_webcall_ai_sessions(db, "worker-b")

    assert [item.id for item in first] == [session.id]
    assert second == []


def test_expired_lease_can_be_reclaimed_deterministically(db):
    session = _voice_session(db, ai_agent_status=WEBCALL_AI_STATUS_CLAIMED, lease_expired=True)

    claimed = claim_webcall_ai_sessions(db, "worker-b")

    assert [item.id for item in claimed] == [session.id]
    db.refresh(session)
    assert session.ai_agent_worker_id == "worker-b"
    assert session.ai_agent_status == WEBCALL_AI_STATUS_CLAIMED


def test_worker_once_cycle_claims_and_releases(db):
    session = _voice_session(db)

    result = run_webcall_ai_worker_once(db, "worker-a", limit=10, lease_seconds=30)

    assert result == {"claimed": 1, "released": 1, "failed": 0, "skipped": 0}
    db.refresh(session)
    assert session.ai_agent_status == WEBCALL_AI_STATUS_RELEASED
