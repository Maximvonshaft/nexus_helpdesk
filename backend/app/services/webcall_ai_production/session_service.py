from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from ...voice_models import WebchatVoiceSession
from ...webchat_models import WebchatConversation, WebchatEvent
from ..webchat_service import create_or_resume_conversation
from ..webchat_voice_service import create_public_voice_session, end_public_voice_session
from .config import get_webcall_ai_production_settings
from .event_service import serialize_event, write_event
from .evidence import hash_tracking_number
from .livekit_service import issue_join_token
from .tools.tracking_lookup import extract_tracking_number


TERMINAL_STATUSES = {"ended", "missed", "failed", "cancelled"}


@dataclass
class WebCallAIInitPayload:
    tenant_key: str = "default"
    channel_key: str = "webcall-ai"
    visitor_name: str | None = None
    visitor_email: str | None = None
    visitor_phone: str | None = None
    visitor_ref: str | None = None
    origin: str | None = None
    page_url: str | None = None
    locale: str | None = None


def _payload_hash(payload: dict[str, Any]) -> str:
    safe = {k: v for k, v in payload.items() if k not in {"visitor_token"}}
    return hashlib.sha256(json.dumps(safe, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _conversation_for_idempotency(db: Session, key: str) -> WebchatConversation | None:
    return (
        db.query(WebchatConversation)
        .filter(
            WebchatConversation.channel_key == "webcall-ai",
            WebchatConversation.visitor_ref == key,
            WebchatConversation.origin == "webcall-ai-production",
        )
        .order_by(WebchatConversation.id.desc())
        .first()
    )


def _active_ai_session(db: Session, conversation: WebchatConversation) -> WebchatVoiceSession | None:
    return (
        db.query(WebchatVoiceSession)
        .filter(
            WebchatVoiceSession.conversation_id == conversation.id,
            WebchatVoiceSession.mode == "livekit_ai_agent",
            WebchatVoiceSession.status.notin_(list(TERMINAL_STATUSES)),
        )
        .order_by(WebchatVoiceSession.id.desc())
        .first()
    )


def _serialize_session(session: WebchatVoiceSession) -> dict[str, Any]:
    return {
        "public_id": session.public_id,
        "status": session.status,
        "provider": session.provider,
        "room_name": session.provider_room_name,
        "mode": session.mode,
        "conversation_id": session.conversation_id,
        "ticket_id": session.ticket_id,
        "ai_agent_status": session.ai_agent_status,
        "ai_turn_count": session.ai_turn_count,
        "started_at": session.started_at.isoformat() if session.started_at else None,
        "ended_at": session.ended_at.isoformat() if session.ended_at else None,
        "expires_at": session.expires_at.isoformat() if session.expires_at else None,
    }


def _load_ai_session(db: Session, session_public_id: str) -> WebchatVoiceSession:
    session = db.query(WebchatVoiceSession).filter(WebchatVoiceSession.public_id == session_public_id, WebchatVoiceSession.mode == "livekit_ai_agent").first()
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="WebCall AI session not found")
    return session


def _validate_visitor_token(db: Session, session: WebchatVoiceSession, visitor_token: str | None) -> WebchatConversation:
    conversation = db.query(WebchatConversation).filter(WebchatConversation.id == session.conversation_id).one()
    if not visitor_token or _hash_token(visitor_token) != conversation.visitor_token_hash:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid WebCall AI visitor token")
    return conversation


def create_session(
    db: Session,
    *,
    request: Request,
    payload: WebCallAIInitPayload,
    idempotency_key: str | None,
) -> dict[str, Any]:
    settings = get_webcall_ai_production_settings()
    if settings.status != "ready":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="WebCall AI production is disabled")
    origin = request.headers.get("origin")
    if settings.allowed_origins and origin and origin not in settings.allowed_origins:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="origin is not allowed for WebCall AI")

    request_payload = {
        "tenant_key": payload.tenant_key,
        "channel_key": "webcall-ai",
        "visitor_name": payload.visitor_name,
        "visitor_email": payload.visitor_email,
        "visitor_phone": payload.visitor_phone,
        "visitor_ref": idempotency_key or payload.visitor_ref,
        "origin": "webcall-ai-production",
        "page_url": payload.page_url,
        "locale": payload.locale,
    }
    idem_hash = _payload_hash(request_payload)

    if idempotency_key:
        existing_conversation = _conversation_for_idempotency(db, idempotency_key)
        if existing_conversation is not None:
            if existing_conversation.fast_issue_key and existing_conversation.fast_issue_key != idem_hash:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="idempotency key payload mismatch")
            active = _active_ai_session(db, existing_conversation)
            if active is not None:
                return {"ok": True, "idempotent": True, "session": _serialize_session(active)}

    conversation_result = create_or_resume_conversation(db, WebCallAIInitPayload(**request_payload), request)
    conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == conversation_result["conversation_id"]).one()
    conversation.channel_key = "webcall-ai"
    conversation.origin = "webcall-ai-production"
    conversation.visitor_ref = idempotency_key or payload.visitor_ref
    conversation.fast_issue_key = idem_hash
    db.flush()

    active_count = (
        db.query(WebchatVoiceSession)
        .filter(WebchatVoiceSession.mode == "livekit_ai_agent", WebchatVoiceSession.status.notin_(list(TERMINAL_STATUSES)))
        .count()
    )
    if active_count >= settings.max_active_sessions:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="maximum active WebCall AI sessions reached")

    created = create_public_voice_session(
        db,
        conversation_public_id=conversation.public_id,
        visitor_token=conversation_result["visitor_token"],
        request=request,
        locale=payload.locale,
        recording_consent=False,
    )
    session = db.query(WebchatVoiceSession).filter(WebchatVoiceSession.public_id == created["voice_session_id"]).one()
    session.mode = "livekit_ai_agent"
    session.ai_agent_status = "waiting_for_worker" if settings.agent_enabled else "agent_disabled"
    session.transcript_status = "pending"
    session.recording_status = "disabled"
    write_event(
        db,
        conversation_id=session.conversation_id,
        ticket_id=session.ticket_id,
        event_type="webcall_ai.session.created",
        payload={"voice_session_id": session.public_id, "agent_enabled": settings.agent_enabled, "raw_audio_persisted": False},
    )
    db.flush()
    return {
        "ok": True,
        "idempotent": False,
        "conversation_id": conversation.public_id,
        "visitor_token": conversation_result["visitor_token"],
        "session": _serialize_session(session),
        "join": {
            "participant_identity": created.get("participant_identity"),
            "participant_token": created.get("participant_token"),
            "expires_in_seconds": created.get("expires_in_seconds"),
            "room_name": created.get("room_name"),
        },
    }


def get_session(db: Session, session_public_id: str, visitor_token: str | None = None, *, require_visitor_token: bool = True) -> dict[str, Any]:
    session = _load_ai_session(db, session_public_id)
    if require_visitor_token:
        _validate_visitor_token(db, session, visitor_token)
    return {"ok": True, "session": _serialize_session(session)}


def create_join_token(db: Session, session_public_id: str, visitor_token: str | None, participant_type: str = "visitor") -> dict[str, Any]:
    session = _load_ai_session(db, session_public_id)
    _validate_visitor_token(db, session, visitor_token)
    if session.status in TERMINAL_STATUSES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="WebCall AI session is closed")
    identity = f"{participant_type}_{session.public_id}"[:160]
    token = issue_join_token(room_name=session.provider_room_name, participant_identity=identity)
    return {"ok": True, **token.__dict__}


def end_session(db: Session, session_public_id: str, visitor_token: str | None) -> dict[str, Any]:
    session = _load_ai_session(db, session_public_id)
    conversation = _validate_visitor_token(db, session, visitor_token)
    result = end_public_voice_session(
        db,
        conversation_public_id=conversation.public_id,
        voice_session_public_id=session.public_id,
        visitor_token=visitor_token,
    )
    write_event(
        db,
        conversation_id=session.conversation_id,
        ticket_id=session.ticket_id,
        event_type="webcall_ai.session.ended",
        payload={"voice_session_id": session.public_id, "status": result["status"]},
    )
    return result


def request_handoff(db: Session, session_public_id: str, visitor_token: str | None, reason: str | None = None) -> dict[str, Any]:
    session = _load_ai_session(db, session_public_id)
    _validate_visitor_token(db, session, visitor_token)
    session.ai_handoff_reason = (reason or "visitor_requested_handoff")[:240]
    session.ai_agent_status = "handoff_requested"
    write_event(
        db,
        conversation_id=session.conversation_id,
        ticket_id=session.ticket_id,
        event_type="webcall_ai.handoff.requested",
        payload={"voice_session_id": session.public_id, "reason": session.ai_handoff_reason},
    )
    return {"ok": True, "session": _serialize_session(session)}


def save_tracking_fallback(db: Session, session_public_id: str, visitor_token: str | None, tracking_number: str) -> dict[str, Any]:
    session = _load_ai_session(db, session_public_id)
    _validate_visitor_token(db, session, visitor_token)
    normalized = extract_tracking_number(tracking_number)
    if not normalized:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="tracking number is invalid")
    write_event(
        db,
        conversation_id=session.conversation_id,
        ticket_id=session.ticket_id,
        event_type="webcall_ai.tracking_fallback.saved",
        payload={
            "voice_session_id": session.public_id,
            "tracking_number_hash": hash_tracking_number(normalized),
            "tracking_number_redacted": f"{normalized[:3]}...{normalized[-2:]}",
        },
    )
    return {"ok": True, "tracking_number_redacted": f"{normalized[:3]}...{normalized[-2:]}"}


def list_events(db: Session, session_public_id: str, visitor_token: str | None = None, *, require_visitor_token: bool = True) -> dict[str, Any]:
    session = _load_ai_session(db, session_public_id)
    if require_visitor_token:
        _validate_visitor_token(db, session, visitor_token)
    events = (
        db.query(WebchatEvent)
        .filter(WebchatEvent.conversation_id == session.conversation_id)
        .order_by(WebchatEvent.id.asc())
        .all()
    )
    return {"ok": True, "session": _serialize_session(session), "events": [serialize_event(event) for event in events]}
