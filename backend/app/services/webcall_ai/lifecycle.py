from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ...utils.time import utc_now
from ...voice_models import WebchatVoiceSession
from ..observability import log_event, record_worker_result
from .config import get_webcall_ai_settings

LOGGER = logging.getLogger(__name__)

WEBCALL_AI_STATUS_PENDING = "pending"
WEBCALL_AI_STATUS_CLAIMED = "claimed"
WEBCALL_AI_STATUS_RELEASED = "released"
WEBCALL_AI_STATUS_FAILED = "failed"
WEBCALL_AI_STATUS_SKIPPED = "skipped"

CLAIMABLE_VOICE_STATUSES = {"created", "ringing"}


@dataclass(frozen=True)
class WebCallAIWorkerResult:
    claimed: int = 0
    released: int = 0
    failed: int = 0
    skipped: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "claimed": self.claimed,
            "released": self.released,
            "failed": self.failed,
            "skipped": self.skipped,
        }


def _lease_deadline(lease_seconds: int):
    return utc_now() + timedelta(seconds=max(1, int(lease_seconds)))


def _base_claim_query(db: Session, *, now):
    return db.query(WebchatVoiceSession).filter(
        WebchatVoiceSession.provider == "livekit",
        WebchatVoiceSession.status.in_(sorted(CLAIMABLE_VOICE_STATUSES)),
        WebchatVoiceSession.accepted_by_user_id.is_(None),
        WebchatVoiceSession.ended_at.is_(None),
        or_(WebchatVoiceSession.expires_at.is_(None), WebchatVoiceSession.expires_at > now),
        or_(
            WebchatVoiceSession.ai_agent_status.is_(None),
            WebchatVoiceSession.ai_agent_status == WEBCALL_AI_STATUS_PENDING,
            and_(
                WebchatVoiceSession.ai_agent_status == WEBCALL_AI_STATUS_CLAIMED,
                WebchatVoiceSession.ai_agent_lease_expires_at.is_not(None),
                WebchatVoiceSession.ai_agent_lease_expires_at <= now,
            ),
        ),
    )


def _apply_row_lock(query, db: Session):
    if getattr(getattr(db, "bind", None), "dialect", None) is not None and db.bind.dialect.name == "postgresql":
        return query.with_for_update(skip_locked=True)
    return query


def claim_webcall_ai_sessions(
    db: Session,
    worker_id: str,
    limit: int = 10,
    lease_seconds: int = 30,
) -> list[WebchatVoiceSession]:
    settings = get_webcall_ai_settings()
    safe_limit = max(0, min(int(limit), 100))
    if not settings.enabled or safe_limit == 0:
        log_event(20, "webcall_ai_claim_skipped", worker_id=worker_id, reason="disabled_or_zero_limit")
        return []

    now = utc_now()
    lease_expires_at = _lease_deadline(lease_seconds)
    query = _base_claim_query(db, now=now).order_by(WebchatVoiceSession.id.asc()).limit(safe_limit)
    sessions = list(_apply_row_lock(query, db).all())

    for session in sessions:
        session.ai_agent_status = WEBCALL_AI_STATUS_CLAIMED
        session.ai_agent_worker_id = worker_id
        session.ai_agent_claimed_at = now
        session.ai_agent_last_heartbeat_at = now
        session.ai_agent_lease_expires_at = lease_expires_at
        session.ai_agent_error_code = None
        session.ai_agent_error_message = None
        session.updated_at = now

    if sessions:
        db.commit()
        for session in sessions:
            db.refresh(session)
        record_worker_result(worker_id, "webcall_ai_session", "claimed", len(sessions))
        log_event(20, "webcall_ai_sessions_claimed", worker_id=worker_id, claimed=len(sessions))
    return sessions


def heartbeat_webcall_ai_session(
    db: Session,
    voice_session_id: int,
    worker_id: str,
    lease_seconds: int = 30,
) -> bool:
    now = utc_now()
    session = (
        db.query(WebchatVoiceSession)
        .filter(
            WebchatVoiceSession.id == voice_session_id,
            WebchatVoiceSession.ai_agent_status == WEBCALL_AI_STATUS_CLAIMED,
            WebchatVoiceSession.ai_agent_worker_id == worker_id,
        )
        .first()
    )
    if session is None:
        return False
    session.ai_agent_last_heartbeat_at = now
    session.ai_agent_lease_expires_at = _lease_deadline(lease_seconds)
    session.updated_at = now
    db.commit()
    return True


def release_webcall_ai_session(
    db: Session,
    voice_session_id: int,
    worker_id: str,
    reason: str | None = None,
) -> bool:
    now = utc_now()
    session = (
        db.query(WebchatVoiceSession)
        .filter(
            WebchatVoiceSession.id == voice_session_id,
            WebchatVoiceSession.ai_agent_status == WEBCALL_AI_STATUS_CLAIMED,
            WebchatVoiceSession.ai_agent_worker_id == worker_id,
        )
        .first()
    )
    if session is None:
        return False
    session.ai_agent_status = WEBCALL_AI_STATUS_RELEASED
    session.ai_agent_ended_at = now
    session.ai_handoff_reason = reason
    session.ai_agent_lease_expires_at = None
    session.updated_at = now
    db.commit()
    record_worker_result(worker_id, "webcall_ai_session", "released", 1)
    return True


def fail_webcall_ai_session(
    db: Session,
    voice_session_id: int,
    worker_id: str,
    error_code: str,
    error_message: str | None = None,
) -> bool:
    now = utc_now()
    session = (
        db.query(WebchatVoiceSession)
        .filter(
            WebchatVoiceSession.id == voice_session_id,
            WebchatVoiceSession.ai_agent_status == WEBCALL_AI_STATUS_CLAIMED,
            WebchatVoiceSession.ai_agent_worker_id == worker_id,
        )
        .first()
    )
    if session is None:
        return False
    session.ai_agent_status = WEBCALL_AI_STATUS_FAILED
    session.ai_agent_ended_at = now
    session.ai_agent_error_code = (error_code or "webcall_ai_worker_failed")[:120]
    session.ai_agent_error_message = error_message
    session.ai_agent_lease_expires_at = None
    session.updated_at = now
    db.commit()
    record_worker_result(worker_id, "webcall_ai_session", "failed", 1)
    return True
