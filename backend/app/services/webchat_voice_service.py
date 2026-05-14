from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import timedelta, timezone
from typing import Any

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from ..models import Ticket, User
from ..utils.time import utc_now
from ..voice_models import WebchatVoiceParticipant, WebchatVoiceSession
from ..webchat_models import WebchatConversation, WebchatEvent, WebchatMessage
from ..webchat_voice_config import WebchatVoiceRuntimeConfig, load_webchat_voice_runtime_config
from .livekit_voice_provider import LiveKitVoiceProvider
from .mock_voice_provider import MockVoiceProvider
from .permissions import ensure_ticket_visible
from .voice_provider import VoiceProvider, VoiceProviderError
from .webchat_rate_limit import enforce_webchat_rate_limit

logger = logging.getLogger(__name__)
TERMINAL_STATUSES = {"ended", "missed", "failed", "cancelled"}
ACTIVE_STATUSES = {"created", "ringing", "accepted", "active"}


def _hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _ensure_aware_utc(value):
    if value is None:
        return None
    if getattr(value, "tzinfo", None) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _validate_public_conversation_token(conversation: WebchatConversation, value: str | None) -> None:
    if not value or _hash_token(value) != conversation.visitor_token_hash:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid webchat visitor token")
    expires_at = _ensure_aware_utc(getattr(conversation, "visitor_token_expires_at", None))
    now = _ensure_aware_utc(utc_now())
    if expires_at is not None and now is not None and expires_at <= now:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid webchat visitor token")


def _new_voice_public_id() -> str:
    return f"wv_{secrets.token_urlsafe(18).replace('-', '').replace('_', '')[:24]}"


def _provider_for_name(provider_name: str, config: WebchatVoiceRuntimeConfig | None = None) -> VoiceProvider:
    provider = (provider_name or "mock").strip().lower()
    if provider == "mock":
        return MockVoiceProvider()
    if provider == "livekit":
        try:
            return LiveKitVoiceProvider.from_config(config or load_webchat_voice_runtime_config())
        except VoiceProviderError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="voice provider is not available in this build")


def _provider(config: WebchatVoiceRuntimeConfig | None = None) -> VoiceProvider:
    runtime_config = config or load_webchat_voice_runtime_config()
    return _provider_for_name(runtime_config.provider, runtime_config)


def _voice_page_url(public_id: str) -> str:
    return f"/webchat/voice/{public_id}"


def _room_name(public_id: str, provider_name: str) -> str:
    return f"{'webcall' if provider_name == 'livekit' else 'webchat'}_{public_id}"


def _participant_identity(session: WebchatVoiceSession, participant_type: str, suffix: str) -> str:
    return f"{participant_type}_{session.public_id}_{suffix}"[:160]


def _write_voice_event(db: Session, *, conversation_id: int, ticket_id: int, event_type: str, payload: dict[str, Any] | None = None) -> None:
    db.add(WebchatEvent(conversation_id=conversation_id, ticket_id=ticket_id, event_type=event_type, payload_json=json.dumps(payload or {}, ensure_ascii=False)))


def _serialize_dt(value: Any) -> str | None:
    return value.isoformat() if value else None


def _serialize_session(session: WebchatVoiceSession, *, participant_token: str | None = None, expires_in_seconds: int | None = None, participant_identity: str | None = None) -> dict[str, Any]:
    return {
        "ok": True,
        "voice_session_id": session.public_id,
        "status": session.status,
        "provider": session.provider,
        "voice_page_url": _voice_page_url(session.public_id),
        "room_name": session.provider_room_name,
        "provider_room_name": session.provider_room_name,
        "participant_identity": participant_identity,
        "participant_token": participant_token,
        "expires_in_seconds": expires_in_seconds,
        "accepted_by_user_id": session.accepted_by_user_id,
        "started_at": _serialize_dt(session.started_at),
        "ringing_at": _serialize_dt(session.ringing_at),
        "accepted_at": _serialize_dt(session.accepted_at),
        "active_at": _serialize_dt(session.active_at),
        "ended_at": _serialize_dt(session.ended_at),
        "expires_at": _serialize_dt(session.expires_at),
    }


def _load_public_conversation(db: Session, public_id: str) -> WebchatConversation:
    conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == public_id).first()
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="webchat conversation not found")
    return conversation


def _query_voice_session(db: Session, voice_session_public_id: str):
    query = db.query(WebchatVoiceSession).filter(WebchatVoiceSession.public_id == voice_session_public_id)
    if db.bind and db.bind.dialect.name.startswith("postgresql"):
        query = query.with_for_update()
    return query


def _load_voice_session(db: Session, voice_session_public_id: str) -> WebchatVoiceSession:
    session = _query_voice_session(db, voice_session_public_id).first()
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="webchat voice session not found")
    return session


def _active_session_for_conversation(db: Session, conversation_id: int) -> WebchatVoiceSession | None:
    return db.query(WebchatVoiceSession).filter(WebchatVoiceSession.conversation_id == conversation_id, WebchatVoiceSession.status.in_(list(ACTIVE_STATUSES))).order_by(WebchatVoiceSession.id.desc()).first()


def _ensure_ticket_visible_for_session(db: Session, current_user: User, session: WebchatVoiceSession) -> Ticket:
    ticket = db.query(Ticket).filter(Ticket.id == session.ticket_id).first()
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ticket not found")
    ensure_ticket_visible(current_user, ticket, db)
    return ticket


def _issue_token(session: WebchatVoiceSession, participant_type: str, suffix: str) -> tuple[str, int, str]:
    config = load_webchat_voice_runtime_config()
    provider = _provider_for_name(session.provider, config)
    identity = _participant_identity(session, participant_type, suffix)
    issued = provider.issue_participant_token(room_name=session.provider_room_name, participant_identity=identity, ttl_seconds=config.session_ttl_seconds)
    return issued.participant_token, issued.expires_in_seconds, identity


def create_public_voice_session(db: Session, *, conversation_public_id: str, visitor_token: str | None, request: Request, locale: str | None = None, recording_consent: bool = False) -> dict[str, Any]:
    config = load_webchat_voice_runtime_config()
    if not config.enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="WebChat voice is disabled")
    conversation = _load_public_conversation(db, conversation_public_id)
    _validate_public_conversation_token(conversation, visitor_token)
    enforce_webchat_rate_limit(db, request, tenant_key=conversation.tenant_key, conversation_id=f"{conversation.public_id}:voice")
    active = _active_session_for_conversation(db, conversation.id)
    if active is not None:
        value, ttl, identity = _issue_token(active, "visitor", "returning")
        return _serialize_session(active, participant_token=value, expires_in_seconds=ttl, participant_identity=identity)

    now = utc_now()
    public_id = _new_voice_public_id()
    provider = _provider(config)
    room_name = _room_name(public_id, provider.provider_name)
    provider.create_room(room_name=room_name)
    try:
        session = WebchatVoiceSession(
            public_id=public_id,
            conversation_id=conversation.id,
            ticket_id=conversation.ticket_id,
            provider=provider.provider_name,
            provider_room_name=room_name,
            status="ringing",
            mode="visitor_to_agent",
            locale=(locale or None),
            recording_consent=bool(recording_consent),
            recording_status="disabled",
            transcript_status="disabled",
            summary_status="pending",
            started_at=now,
            ringing_at=now,
            expires_at=now + timedelta(seconds=config.session_ttl_seconds),
            created_at=now,
            updated_at=now,
        )
        db.add(session)
        db.flush()
        value, ttl, identity = _issue_token(session, "visitor", "initial")
        db.add(WebchatVoiceParticipant(voice_session_id=session.id, participant_type="visitor", visitor_label=conversation.visitor_name or "Visitor", provider_identity=identity, status="invited", created_at=now))
        _write_voice_event(db, conversation_id=conversation.id, ticket_id=conversation.ticket_id, event_type="voice.session.created", payload={"voice_session_id": session.public_id, "provider": session.provider, "room_name": session.provider_room_name})
        _write_voice_event(db, conversation_id=conversation.id, ticket_id=conversation.ticket_id, event_type="voice.session.ringing", payload={"voice_session_id": session.public_id})
        db.flush()
        return _serialize_session(session, participant_token=value, expires_in_seconds=ttl, participant_identity=identity)
    except Exception:
        try:
            provider.close_room(room_name=room_name)
        except Exception as close_exc:
            logger.warning("voice_provider_room_compensation_failed", extra={"room_name": room_name, "provider": provider.provider_name, "error_type": type(close_exc).__name__})
        raise


def end_public_voice_session(db: Session, *, conversation_public_id: str, voice_session_public_id: str, visitor_token: str | None) -> dict[str, Any]:
    conversation = _load_public_conversation(db, conversation_public_id)
    _validate_public_conversation_token(conversation, visitor_token)
    session = _load_voice_session(db, voice_session_public_id)
    if session.conversation_id != conversation.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="webchat voice session not found")
    _end_voice_session(db, session=session, ended_by_user_id=None)
    return {"ok": True, "status": session.status, "voice_session_id": session.public_id, "accepted_by_user_id": session.accepted_by_user_id}


def list_admin_voice_sessions(db: Session, *, ticket_id: int, current_user: User) -> dict[str, Any]:
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ticket not found")
    ensure_ticket_visible(current_user, ticket, db)
    sessions = db.query(WebchatVoiceSession).filter(WebchatVoiceSession.ticket_id == ticket_id).order_by(WebchatVoiceSession.id.desc()).limit(20).all()
    return {"items": [_serialize_session(session) for session in sessions]}


def accept_admin_voice_session(db: Session, *, ticket_id: int, voice_session_public_id: str, current_user: User) -> dict[str, Any]:
    session = _load_voice_session(db, voice_session_public_id)
    if session.ticket_id != ticket_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="webchat voice session not found")
    _ensure_ticket_visible_for_session(db, current_user, session)
    if session.status in TERMINAL_STATUSES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="voice session is already closed")
    if session.accepted_by_user_id is not None and session.accepted_by_user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="voice session already accepted")
    now = utc_now()
    session.status = "active"
    session.accepted_by_user_id = current_user.id
    session.accepted_at = session.accepted_at or now
    session.active_at = session.active_at or now
    session.updated_at = now
    value, ttl, identity = _issue_token(session, "agent", str(current_user.id))
    existing = db.query(WebchatVoiceParticipant).filter(WebchatVoiceParticipant.voice_session_id == session.id, WebchatVoiceParticipant.provider_identity == identity).first()
    if existing is None:
        db.add(WebchatVoiceParticipant(voice_session_id=session.id, participant_type="agent", user_id=current_user.id, provider_identity=identity, status="invited", created_at=now))
    _write_voice_event(db, conversation_id=session.conversation_id, ticket_id=session.ticket_id, event_type="voice.session.accepted", payload={"voice_session_id": session.public_id, "accepted_by_user_id": current_user.id})
    _write_voice_event(db, conversation_id=session.conversation_id, ticket_id=session.ticket_id, event_type="voice.session.active", payload={"voice_session_id": session.public_id, "accepted_by_user_id": current_user.id})
    db.flush()
    return _serialize_session(session, participant_token=value, expires_in_seconds=ttl, participant_identity=identity)


def end_admin_voice_session(db: Session, *, ticket_id: int, voice_session_public_id: str, current_user: User) -> dict[str, Any]:
    session = _load_voice_session(db, voice_session_public_id)
    if session.ticket_id != ticket_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="webchat voice session not found")
    _ensure_ticket_visible_for_session(db, current_user, session)
    _end_voice_session(db, session=session, ended_by_user_id=current_user.id)
    return {"ok": True, "status": session.status, "voice_session_id": session.public_id, "accepted_by_user_id": session.accepted_by_user_id}


def _close_provider_room_for_session(session: WebchatVoiceSession) -> None:
    try:
        _provider_for_name(session.provider).close_room(room_name=session.provider_room_name)
    except Exception as exc:
        logger.warning("voice_provider_room_close_skipped", extra={"voice_session_id": session.public_id, "provider": session.provider, "error_type": type(exc).__name__})


def _end_voice_session(db: Session, *, session: WebchatVoiceSession, ended_by_user_id: int | None) -> None:
    if session.status in TERMINAL_STATUSES:
        return
    now = utc_now()
    session.status = "ended" if session.status in {"accepted", "active"} else "cancelled"
    session.ended_at = session.ended_at or now
    session.ended_by_user_id = ended_by_user_id
    session.updated_at = now
    _close_provider_room_for_session(session)
    _write_voice_event(db, conversation_id=session.conversation_id, ticket_id=session.ticket_id, event_type="voice.session.ended" if session.status == "ended" else "voice.session.cancelled", payload={"voice_session_id": session.public_id, "ended_by_user_id": ended_by_user_id})
    _ensure_final_voice_call_message(db, session=session)
    db.flush()


def _ensure_final_voice_call_message(db: Session, *, session: WebchatVoiceSession) -> None:
    client_message_id = f"voice-call-ended:{session.public_id}"
    existing = db.query(WebchatMessage).filter(WebchatMessage.conversation_id == session.conversation_id, WebchatMessage.client_message_id == client_message_id).first()
    if existing is not None:
        return
    duration_seconds = None
    started_at = _ensure_aware_utc(session.started_at)
    ended_at = _ensure_aware_utc(session.ended_at)
    if started_at and ended_at:
        duration_seconds = max(0, int((ended_at - started_at).total_seconds()))
    body = "Voice call ended" if session.status == "ended" else "Voice call cancelled"
    if duration_seconds is not None:
        body = f"{body} · {duration_seconds}s"
    db.add(WebchatMessage(
        conversation_id=session.conversation_id,
        ticket_id=session.ticket_id,
        direction="system",
        body=body,
        body_text=body,
        message_type="voice_call",
        payload_json=json.dumps({"voice_session_id": session.public_id, "status": session.status, "provider": session.provider, "duration_seconds": duration_seconds, "accepted_by_user_id": session.accepted_by_user_id, "recording_status": session.recording_status, "transcript_status": session.transcript_status, "summary_status": session.summary_status}, ensure_ascii=False),
        metadata_json=json.dumps({"generated_by": "system", "external_send": False}, ensure_ascii=False),
        client_message_id=client_message_id,
        delivery_status="sent",
        author_label="NexusDesk Voice",
    ))
