from __future__ import annotations

import re

from sqlalchemy.orm import Session

from ...services.voice_provider import VoiceParticipantToken
from ...utils.time import utc_now
from ...voice_models import WebchatVoiceParticipant, WebchatVoiceSession
from .config import WebCallAISettings, get_webcall_ai_settings

AI_PARTICIPANT_LABEL = "AI Voice Agent"
AI_PARTICIPANT_TYPE = "ai"

_SAFE_IDENTITY_CHARS = re.compile(r"[^A-Za-z0-9_-]+")


def ai_participant_identity(
    session: WebchatVoiceSession,
    settings: WebCallAISettings | None = None,
) -> str:
    resolved = settings or get_webcall_ai_settings()
    raw_session_id = str(session.public_id or session.id)
    safe_session_id = _SAFE_IDENTITY_CHARS.sub("_", raw_session_id).strip("_") or str(session.id)
    safe_prefix = _SAFE_IDENTITY_CHARS.sub("_", resolved.participant_id_prefix).strip("_") or "ai_webcall"
    identity = f"{safe_prefix}_{safe_session_id}"
    return identity[:160]


def ensure_ai_participant_record(
    db: Session,
    *,
    session: WebchatVoiceSession,
    worker_id: str,
    token: VoiceParticipantToken | None = None,
    settings: WebCallAISettings | None = None,
) -> WebchatVoiceParticipant:
    identity = ai_participant_identity(session, settings)
    participant = (
        db.query(WebchatVoiceParticipant)
        .filter(
            WebchatVoiceParticipant.voice_session_id == session.id,
            WebchatVoiceParticipant.provider_identity == identity,
        )
        .one_or_none()
    )
    if participant is None:
        participant = WebchatVoiceParticipant(
            voice_session_id=session.id,
            participant_type=AI_PARTICIPANT_TYPE,
            user_id=None,
            visitor_label=AI_PARTICIPANT_LABEL,
            provider_identity=identity,
            status="invited",
            created_at=utc_now(),
        )
        db.add(participant)

    participant.participant_type = AI_PARTICIPANT_TYPE
    participant.user_id = None
    participant.visitor_label = AI_PARTICIPANT_LABEL
    if token is not None:
        participant.status = "token_issued"
    db.flush()
    return participant


def mark_ai_participant_joined(
    db: Session,
    *,
    session: WebchatVoiceSession,
    worker_id: str,
    settings: WebCallAISettings | None = None,
) -> bool:
    participant = _find_ai_participant(db, session=session, settings=settings)
    if participant is None:
        return False
    participant.status = "joined"
    participant.joined_at = utc_now()
    db.flush()
    return True


def mark_ai_participant_left(
    db: Session,
    *,
    session: WebchatVoiceSession,
    worker_id: str,
    reason: str,
    settings: WebCallAISettings | None = None,
) -> bool:
    participant = _find_ai_participant(db, session=session, settings=settings)
    if participant is None:
        return False
    participant.status = "left"
    participant.left_at = utc_now()
    db.flush()
    return True


def mark_ai_participant_failed(
    db: Session,
    *,
    session: WebchatVoiceSession,
    worker_id: str,
    settings: WebCallAISettings | None = None,
) -> bool:
    participant = _find_ai_participant(db, session=session, settings=settings)
    if participant is None:
        return False
    participant.status = "failed"
    db.flush()
    return True


def _find_ai_participant(
    db: Session,
    *,
    session: WebchatVoiceSession,
    settings: WebCallAISettings | None = None,
) -> WebchatVoiceParticipant | None:
    identity = ai_participant_identity(session, settings)
    return (
        db.query(WebchatVoiceParticipant)
        .filter(
            WebchatVoiceParticipant.voice_session_id == session.id,
            WebchatVoiceParticipant.provider_identity == identity,
        )
        .one_or_none()
    )
