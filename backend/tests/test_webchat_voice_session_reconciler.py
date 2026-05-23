from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Ticket, User  # noqa: F401 - registers referenced tables
from app.services.webchat_voice_session_reconciler import reconcile_stale_webchat_voice_sessions
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


def _add_session(
    db,
    *,
    public_id: str,
    status: str,
    now: datetime,
    expires_delta: timedelta,
    accepted: bool = False,
    active: bool = False,
    ended: bool = False,
    provider_room_name: str | None = None,
) -> WebchatVoiceSession:
    row = WebchatVoiceSession(
        public_id=public_id,
        conversation_id=1,
        ticket_id=1,
        provider="mock",
        provider_room_name=provider_room_name or f"webchat_{public_id}",
        status=status,
        started_at=now - timedelta(minutes=10),
        ringing_at=now - timedelta(minutes=10),
        accepted_at=now - timedelta(minutes=8) if accepted else None,
        active_at=now - timedelta(minutes=8) if active else None,
        ended_at=now - timedelta(minutes=1) if ended else None,
        expires_at=now + expires_delta,
        created_at=now - timedelta(minutes=10),
        updated_at=now - timedelta(minutes=10),
    )
    db.add(row)
    db.flush()
    return row


def test_dry_run_stale_active_does_not_write(db_session):
    now = datetime(2026, 5, 23, 12, 0, tzinfo=timezone.utc)
    row = _add_session(db_session, public_id="wv_active_dry", status="active", now=now, expires_delta=timedelta(minutes=-10), active=True)

    result = reconcile_stale_webchat_voice_sessions(db_session, now=now, dry_run=True, older_than_seconds=300)

    db_session.refresh(row)
    assert result.dry_run is True
    assert result.eligible_count == 1
    assert result.updated_count == 0
    assert result.items[0].target_status == "ended"
    assert result.items[0].action == "would_update"
    assert row.status == "active"
    assert row.ended_at is None


def test_apply_stale_active_becomes_ended(db_session):
    now = datetime(2026, 5, 23, 12, 0, tzinfo=timezone.utc)
    row = _add_session(db_session, public_id="wv_active_apply", status="active", now=now, expires_delta=timedelta(minutes=-10), active=True)

    result = reconcile_stale_webchat_voice_sessions(db_session, now=now, dry_run=False, older_than_seconds=300)

    db_session.refresh(row)
    assert result.updated_count == 1
    assert result.by_target_status == {"ended": 1}
    assert row.status == "ended"
    assert row.ended_at == row.expires_at


def test_apply_stale_ringing_becomes_missed(db_session):
    now = datetime(2026, 5, 23, 12, 0, tzinfo=timezone.utc)
    row = _add_session(db_session, public_id="wv_ringing_apply", status="ringing", now=now, expires_delta=timedelta(minutes=-10))

    result = reconcile_stale_webchat_voice_sessions(db_session, now=now, dry_run=False, older_than_seconds=300)

    db_session.refresh(row)
    assert result.updated_count == 1
    assert result.by_target_status == {"missed": 1}
    assert row.status == "missed"
    assert row.ended_at == row.expires_at


def test_terminal_and_non_expired_sessions_are_skipped(db_session):
    now = datetime(2026, 5, 23, 12, 0, tzinfo=timezone.utc)
    terminal = _add_session(db_session, public_id="wv_terminal", status="ended", now=now, expires_delta=timedelta(minutes=-10), ended=True)
    fresh = _add_session(db_session, public_id="wv_fresh", status="ringing", now=now, expires_delta=timedelta(minutes=10))

    result = reconcile_stale_webchat_voice_sessions(db_session, now=now, dry_run=False, older_than_seconds=300)

    db_session.refresh(terminal)
    db_session.refresh(fresh)
    assert result.eligible_count == 0
    assert result.updated_count == 0
    assert terminal.status == "ended"
    assert fresh.status == "ringing"


def test_limit_is_respected(db_session):
    now = datetime(2026, 5, 23, 12, 0, tzinfo=timezone.utc)
    _add_session(db_session, public_id="wv_one", status="ringing", now=now, expires_delta=timedelta(minutes=-20))
    _add_session(db_session, public_id="wv_two", status="ringing", now=now, expires_delta=timedelta(minutes=-10))

    result = reconcile_stale_webchat_voice_sessions(db_session, now=now, dry_run=False, limit=1, older_than_seconds=300)

    assert result.eligible_count == 2
    assert result.processed_count == 1
    assert result.updated_count == 1
    assert result.skipped_count == 1
    assert result.warnings


def test_output_does_not_include_pii_or_provider_room_name(db_session):
    now = datetime(2026, 5, 23, 12, 0, tzinfo=timezone.utc)
    pii = "visitor@example.test"
    _add_session(
        db_session,
        public_id="wv_safe_output",
        status="ringing",
        now=now,
        expires_delta=timedelta(minutes=-10),
        provider_room_name=f"webchat_{pii}",
    )

    result = reconcile_stale_webchat_voice_sessions(db_session, now=now, dry_run=True, older_than_seconds=300)

    rendered = str(result.to_safe_dict())
    assert pii not in rendered
    assert "webchat_visitor" not in rendered
    assert "wv_safe_output" in rendered


@pytest.mark.parametrize(
    ("limit", "older_than_seconds", "message"),
    [
        (0, 0, "limit"),
        (1001, 0, "limit"),
        (1, -1, "older_than_seconds"),
    ],
)
def test_invalid_bounds_rejected(db_session, limit: int, older_than_seconds: int, message: str):
    with pytest.raises(ValueError, match=message):
        reconcile_stale_webchat_voice_sessions(
            db_session,
            limit=limit,
            older_than_seconds=older_than_seconds,
        )
