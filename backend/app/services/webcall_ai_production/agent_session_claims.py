from __future__ import annotations

from datetime import timedelta

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ...utils.time import utc_now
from ...voice_models import WebchatVoiceSession
from .config import get_webcall_ai_production_settings

AI_STATUS_WAITING = "waiting_for_worker"
AI_STATUS_CLAIMED = "claimed"
AI_STATUS_JOINING = "joining"
AI_STATUS_JOINED = "joined"
AI_STATUS_LISTENING = "listening"
AI_STATUS_THINKING = "thinking"
AI_STATUS_SPEAKING = "speaking"
AI_STATUS_RELEASED = "released"
AI_STATUS_FAILED = "failed"
AI_STATUS_HANDOFF_REQUESTED = "handoff_requested"

CLAIMABLE_SESSION_STATUSES = {"created", "ringing", "active"}
TERMINAL_SESSION_STATUSES = {"ended", "missed", "failed", "cancelled"}


def _lease_expires_at(lease_seconds: int):
    return utc_now() + timedelta(seconds=max(1, int(lease_seconds)))


def _claim_query(db: Session, *, now):
    return db.query(WebchatVoiceSession).filter(
        WebchatVoiceSession.mode == "livekit_ai_agent",
        WebchatVoiceSession.status.in_(sorted(CLAIMABLE_SESSION_STATUSES)),
        WebchatVoiceSession.accepted_by_user_id.is_(None),
        WebchatVoiceSession.ended_at.is_(None),
        or_(WebchatVoiceSession.expires_at.is_(None), WebchatVoiceSession.expires_at > now),
        or_(
            WebchatVoiceSession.ai_agent_status.is_(None),
            WebchatVoiceSession.ai_agent_status == AI_STATUS_WAITING,
            and_(
                WebchatVoiceSession.ai_agent_status.in_([AI_STATUS_CLAIMED, AI_STATUS_JOINING, AI_STATUS_JOINED, AI_STATUS_LISTENING]),
                WebchatVoiceSession.ai_agent_lease_expires_at.is_not(None),
                WebchatVoiceSession.ai_agent_lease_expires_at <= now,
            ),
        ),
    )


def _lock_if_supported(query, db: Session):
    if getattr(getattr(db, "bind", None), "dialect", None) is not None and db.bind.dialect.name == "postgresql":
        return query.with_for_update(skip_locked=True)
    return query


def claim_next_session(db: Session, *, worker_id: str, lease_seconds: int | None = None) -> WebchatVoiceSession | None:
    settings = get_webcall_ai_production_settings()
    if not settings.production_enabled or not settings.agent_enabled or settings.kill_switch:
        return None
    now = utc_now()
    session = _lock_if_supported(_claim_query(db, now=now).order_by(WebchatVoiceSession.id.asc()).limit(1), db).first()
    if session is None:
        return None
    session.ai_agent_status = AI_STATUS_CLAIMED
    session.ai_agent_worker_id = worker_id
    session.ai_agent_claimed_at = now
    session.ai_agent_last_heartbeat_at = now
    session.ai_agent_lease_expires_at = _lease_expires_at(lease_seconds or settings.agent_lease_seconds)
    session.ai_agent_error_code = None
    session.ai_agent_error_message = None
    session.updated_at = now
    db.commit()
    db.refresh(session)
    return session


def mark_status(db: Session, *, session_id: int, worker_id: str, status: str, lease_seconds: int | None = None) -> bool:
    settings = get_webcall_ai_production_settings()
    session = (
        db.query(WebchatVoiceSession)
        .filter(WebchatVoiceSession.id == session_id, WebchatVoiceSession.ai_agent_worker_id == worker_id)
        .first()
    )
    if session is None:
        return False
    now = utc_now()
    session.ai_agent_status = status
    session.ai_agent_last_heartbeat_at = now
    session.ai_agent_lease_expires_at = _lease_expires_at(lease_seconds or settings.agent_lease_seconds)
    if status == AI_STATUS_JOINED and session.ai_agent_started_at is None:
        session.ai_agent_started_at = now
    session.updated_at = now
    db.commit()
    return True


TERMINAL_RELEASE_REASONS = {"visitor_disconnected", "max_session_seconds", "max_turns", "session_ended", "kill_switch"}


def release_session(db: Session, *, session_id: int, worker_id: str, reason: str | None = None) -> bool:
    session = (
        db.query(WebchatVoiceSession)
        .filter(WebchatVoiceSession.id == session_id, WebchatVoiceSession.ai_agent_worker_id == worker_id)
        .first()
    )
    if session is None:
        return False
    now = utc_now()
    session.ai_agent_status = AI_STATUS_RELEASED
    session.ai_agent_ended_at = now
    session.ai_handoff_reason = reason
    session.ai_agent_lease_expires_at = None
    if reason in TERMINAL_RELEASE_REASONS:
        session.status = "ended"
        session.ended_at = session.ended_at or now
    elif reason == "handoff_required":
        session.ai_agent_status = AI_STATUS_HANDOFF_REQUESTED
    session.updated_at = now
    db.commit()
    return True


def should_continue_session(db: Session, *, session_id: int, worker_id: str) -> tuple[bool, str]:
    settings = get_webcall_ai_production_settings()
    if settings.kill_switch:
        return False, "kill_switch"
    session = (
        db.query(WebchatVoiceSession)
        .filter(WebchatVoiceSession.id == session_id, WebchatVoiceSession.ai_agent_worker_id == worker_id)
        .first()
    )
    if session is None:
        return False, "session_missing"
    if session.status in TERMINAL_SESSION_STATUSES or session.ended_at is not None:
        return False, "session_ended"
    if session.ai_agent_status == AI_STATUS_HANDOFF_REQUESTED:
        return False, "handoff_requested"
    if int(session.ai_turn_count or 0) >= settings.max_turns_per_session:
        return False, "max_turns"
    return True, "continue"


def fail_session(db: Session, *, session_id: int, worker_id: str, error_code: str, error_message: str | None = None) -> bool:
    session = (
        db.query(WebchatVoiceSession)
        .filter(WebchatVoiceSession.id == session_id, WebchatVoiceSession.ai_agent_worker_id == worker_id)
        .first()
    )
    if session is None:
        return False
    now = utc_now()
    session.ai_agent_status = AI_STATUS_FAILED
    session.ai_agent_ended_at = now
    session.ai_agent_error_code = (error_code or "webcall_ai_agent_failed")[:120]
    session.ai_agent_error_message = (error_message or "")[:1000] or None
    session.ai_agent_lease_expires_at = None
    session.updated_at = now
    db.commit()
    return True
