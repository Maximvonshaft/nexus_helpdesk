from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
from datetime import timedelta, timezone
from typing import Any

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from ..enums import EventType
from ..models import ChannelAccount, Ticket, TicketInternalNote, User
from ..models_agent_routing import ConversationControl
from ..utils.time import utc_now
from ..voice_models import VoiceChannelConfiguration, WebchatVoiceAIAction, WebchatVoiceAITurn, WebchatVoiceParticipant, WebchatVoiceSession, WebchatVoiceSessionAction, WebchatVoiceTranscriptSegment
from ..webchat_models import WebchatConversation, WebchatEvent, WebchatMessage
from ..webchat_voice_config import WebchatVoiceRuntimeConfig, load_webchat_voice_runtime_config
from .livekit_voice_provider import LiveKitVoiceProvider
from .mock_voice_provider import MockVoiceProvider
from .observability import (
    log_event as app_log_event,
    record_voice_call_duration,
    record_voice_provider_error,
    record_voice_ringing_duration,
    record_voice_session_event,
)
from .background_jobs import enqueue_speedaf_voice_callback_job, find_recent_speedaf_voice_callback_job
from .permissions import (
    ensure_can_accept_webcall_voice,
    ensure_can_control_webcall_voice,
    ensure_can_end_webcall_voice,
    ensure_can_read_webcall_voice,
    ensure_can_reject_webcall_voice,
    ensure_can_send_speedaf_voice_callback,
    ensure_can_write_internal_note,
    ensure_can_view_webcall_voice_queue,
    ensure_ticket_visible,
)
from .speedaf.redactor import safe_waybill_payload
from .audit_service import log_admin_audit, log_event
from .conversation_operator_service import ensure_conversation_visible
from .agent_routing_service import (
    assign_handoff_to_agent,
    decline_voice_handoff_offer,
    get_or_create_agent_state,
    request_handoff,
)
from .voice_provider import VoiceProvider, VoiceProviderError
from .webchat_rate_limit import enforce_webchat_rate_limit

logger = logging.getLogger(__name__)
TERMINAL_STATUSES = {"ended", "missed", "failed", "cancelled"}
ACTIVE_STATUSES = {"created", "ringing", "accepted", "active"}
ACCEPT_READY_STATUSES = {"created", "ringing"}
ACCEPTED_STATUSES = {"accepted", "active"}
REJECT_READY_STATUSES = {"created", "ringing"}
CALL_CONTROL_ACTIONS = {"hold", "resume", "mute", "unmute", "keypad", "transfer", "add_participant"}
CALL_CONTROL_ACTIVE_STATUSES = {"accepted", "active"}

DETAIL_ALREADY_ACCEPTED_BY_OTHER = "voice session already accepted by another agent"
DETAIL_ALREADY_ACTIVE = "voice session already active"
DETAIL_EXPIRED = "voice session expired"
DETAIL_MISSED = "voice session missed"
DETAIL_ENDED = "voice session ended"
DETAIL_FAILED = "voice session failed"
DETAIL_CANCELLED = "voice session cancelled"
DETAIL_NOT_ACCEPTABLE = "voice session cannot be accepted from current status"
DETAIL_NOT_REJECTABLE = "voice session cannot be rejected from current status"


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


def _write_voice_event(db: Session, *, conversation_id: int, ticket_id: int | None, event_type: str, payload: dict[str, Any] | None = None) -> WebchatEvent:
    row = WebchatEvent(conversation_id=conversation_id, ticket_id=ticket_id, event_type=event_type, payload_json=json.dumps(payload or {}, ensure_ascii=False))
    db.add(row)
    return row


def _voice_duration_seconds(started_at: Any, ended_at: Any) -> int | None:
    start = _ensure_aware_utc(started_at)
    end = _ensure_aware_utc(ended_at)
    if not start or not end:
        return None
    return max(0, int((end - start).total_seconds()))


def _emit_voice_observability(session: WebchatVoiceSession, event_type: str) -> None:
    record_voice_session_event(session.provider, session.status, event_type)
    app_log_event(
        20,
        "voice_session_lifecycle",
        voice_session_id=session.public_id,
        conversation_id=session.conversation_id,
        ticket_id=session.ticket_id,
        provider=session.provider,
        status=session.status,
        event_type=event_type,
        accepted_by_user_id=session.accepted_by_user_id,
        ended_by_user_id=session.ended_by_user_id,
    )


def _serialize_incoming_session(session: WebchatVoiceSession, ticket: Ticket | None, conversation: WebchatConversation) -> dict[str, Any]:
    payload = _serialize_session(session)
    visitor_label = conversation.visitor_name or conversation.visitor_email or conversation.visitor_phone or "Anonymous visitor"
    payload.update(
        {
            "ticket_id": ticket.id if ticket is not None else None,
            "ticket_no": getattr(ticket, "ticket_no", None) if ticket is not None else None,
            "ticket_title": getattr(ticket, "title", None) if ticket is not None else None,
            "conversation_id": conversation.public_id,
            "visitor_label": visitor_label,
            "origin": conversation.origin,
            "page_url": conversation.page_url,
        }
    )
    payload.pop("participant_token", None)
    payload.pop("participant_identity", None)
    return payload


def _serialize_dt(value: Any) -> str | None:
    return value.isoformat() if value else None


def _voice_evidence_payload(session: WebchatVoiceSession) -> dict[str, Any]:
    ringing_duration_seconds = _voice_duration_seconds(session.ringing_at, session.accepted_at or session.ended_at)
    talk_duration_seconds = _voice_duration_seconds(session.accepted_at or session.active_at, session.ended_at)
    total_duration_seconds = _voice_duration_seconds(session.started_at, session.ended_at)
    return {
        "voice_session_id": session.public_id,
        "status": session.status,
        "provider": session.provider,
        "accepted_by": session.accepted_by_user_id,
        "accepted_by_user_id": session.accepted_by_user_id,
        "ended_by": session.ended_by_user_id,
        "ended_by_user_id": session.ended_by_user_id,
        "ringing_duration_seconds": ringing_duration_seconds,
        "talk_duration_seconds": talk_duration_seconds,
        "total_duration_seconds": total_duration_seconds,
        "duration_seconds": total_duration_seconds,
        "recording_status": session.recording_status,
        "transcript_status": session.transcript_status,
        "summary_status": session.summary_status,
    }


def _serialize_session(
    session: WebchatVoiceSession,
    *,
    participant_token: str | None = None,
    expires_in_seconds: int | None = None,
    participant_identity: str | None = None,
) -> dict[str, Any]:
    livekit_url = None
    if session.provider == "livekit":
        try:
            livekit_url = load_webchat_voice_runtime_config().livekit_url
        except Exception:
            livekit_url = None
    payload = {
        "ok": True,
        "voice_session_id": session.public_id,
        "status": session.status,
        "provider": session.provider,
        "livekit_url": livekit_url,
        "voice_page_url": _voice_page_url(session.public_id),
        "room_name": session.provider_room_name,
        "provider_room_name": session.provider_room_name,
        "participant_identity": participant_identity,
        "participant_token": participant_token,
        "expires_in_seconds": expires_in_seconds,
        "handoff_request_id": session.handoff_request_id,
        "accepted_by_user_id": session.accepted_by_user_id,
        "ended_by_user_id": session.ended_by_user_id,
        "mode": session.mode,
        "direction": session.direction,
        "ai_agent_status": session.ai_agent_status,
        "started_at": _serialize_dt(session.started_at),
        "ringing_at": _serialize_dt(session.ringing_at),
        "accepted_at": _serialize_dt(session.accepted_at),
        "active_at": _serialize_dt(session.active_at),
        "ended_at": _serialize_dt(session.ended_at),
        "wrap_up_expires_at": _serialize_dt(session.wrap_up_expires_at),
        "expires_at": _serialize_dt(session.expires_at),
        "recording_status": session.recording_status,
        "transcript_status": session.transcript_status,
        "summary_status": session.summary_status,
    }
    payload.update(
        {
            key: value
            for key, value in _voice_evidence_payload(session).items()
            if key.endswith("_duration_seconds")
        }
    )
    return payload


def _safe_action_payload(action_type: str, *, target: str | None = None, digits: str | None = None, note: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if target:
        payload["target"] = target[:240]
    if digits:
        payload["digits_length"] = len(digits)
        payload["digits_redacted"] = "*" * min(len(digits), 8)
    if note:
        payload["note"] = note[:500]
    if action_type in {"hold", "resume", "mute", "unmute"}:
        payload["client_media_state"] = action_type
    return payload


def _serialize_session_action(action: WebchatVoiceSessionAction) -> dict[str, Any]:
    try:
        payload = json.loads(action.payload_json or "{}")
    except Exception:
        payload = {}
    return {
        "id": action.id,
        "action_type": action.action_type,
        "idempotency_key": action.idempotency_key,
        "status": action.status,
        "provider_status": action.provider_status,
        "provider_reason": action.provider_reason,
        "attempt_count": action.attempt_count,
        "payload": payload,
        "actor_user_id": action.actor_user_id,
        "ticket_event_id": action.ticket_event_id,
        "webchat_event_id": action.webchat_event_id,
        "audit_id": action.audit_id,
        "completed_at": _serialize_dt(action.completed_at),
        "created_at": _serialize_dt(action.created_at),
    }


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def _closed_accept_detail(session_status: str | None) -> str:
    if session_status == "ended":
        return DETAIL_ENDED
    if session_status == "missed":
        return DETAIL_MISSED
    if session_status == "failed":
        return DETAIL_FAILED
    if session_status == "cancelled":
        return DETAIL_CANCELLED
    return DETAIL_NOT_ACCEPTABLE


def _session_expired(session: WebchatVoiceSession, *, now: Any | None = None) -> bool:
    expires_at = _ensure_aware_utc(getattr(session, "expires_at", None))
    if expires_at is None:
        return False
    current = _ensure_aware_utc(now or utc_now())
    return current is not None and expires_at <= current


def _mark_missed_if_expired(db: Session, *, session: WebchatVoiceSession, now: Any | None = None) -> bool:
    if session.status not in {"created", "ringing"}:
        return False
    current = _ensure_aware_utc(now or utc_now())
    if not _session_expired(session, now=current):
        return False
    session.status = "missed"
    session.ended_at = session.ended_at or current
    session.updated_at = current
    _close_provider_room_for_session(session)
    _write_voice_event(
        db,
        conversation_id=session.conversation_id,
        ticket_id=session.ticket_id,
        event_type="voice.session.missed",
        payload={"voice_session_id": session.public_id, "reason": "expired"},
    )
    _emit_voice_observability(session, "voice.session.missed")
    record_voice_call_duration(session.provider, session.status, _voice_duration_seconds(session.started_at, session.ended_at))
    record_voice_ringing_duration(session.provider, session.status, _voice_duration_seconds(session.ringing_at, session.ended_at))
    _ensure_final_voice_call_message(db, session=session)
    return True


def _cleanup_expired_ringing_sessions(db: Session, *, limit: int = 200) -> int:
    current = utc_now()
    rows = (
        db.query(WebchatVoiceSession)
        .filter(
            WebchatVoiceSession.status.in_(["created", "ringing"]),
            WebchatVoiceSession.expires_at.isnot(None),
            WebchatVoiceSession.expires_at <= current,
        )
        .order_by(WebchatVoiceSession.expires_at.asc(), WebchatVoiceSession.id.asc())
        .limit(max(1, min(int(limit or 200), 500)))
        .all()
    )
    changed = 0
    for session in rows:
        if _mark_missed_if_expired(db, session=session, now=current):
            changed += 1
    if changed:
        db.flush()
    return changed


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


def _ensure_voice_session_visible(
    db: Session,
    current_user: User,
    session: WebchatVoiceSession,
    conversation: WebchatConversation,
) -> Ticket | None:
    if session.ticket_id is not None:
        ticket = db.query(Ticket).filter(Ticket.id == session.ticket_id).first()
        if ticket is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ticket not found")
        ensure_ticket_visible(current_user, ticket, db)
        return ticket
    ensure_conversation_visible(db, conversation=conversation, user=current_user)
    return None


def _load_voice_session_context(
    db: Session,
    voice_session_public_id: str,
) -> tuple[WebchatVoiceSession, WebchatConversation]:
    session = _load_voice_session(db, voice_session_public_id)
    conversation = db.get(WebchatConversation, session.conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="webchat conversation not found")
    return session, conversation


def _visible_voice_session_context(
    db: Session,
    *,
    voice_session_public_id: str,
    current_user: User,
) -> tuple[WebchatVoiceSession, WebchatConversation, Ticket | None]:
    session, conversation = _load_voice_session_context(db, voice_session_public_id)
    ticket = _ensure_voice_session_visible(db, current_user, session, conversation)
    return session, conversation, ticket


def _issue_token(session: WebchatVoiceSession, participant_type: str, suffix: str) -> tuple[str, int, str]:
    config = load_webchat_voice_runtime_config()
    provider = _provider_for_name(session.provider, config)
    identity = _participant_identity(session, participant_type, suffix)
    issued = provider.issue_participant_token(room_name=session.provider_room_name, participant_identity=identity, ttl_seconds=config.session_ttl_seconds)
    return issued.participant_token, issued.expires_in_seconds, identity


def create_public_voice_session(
    db: Session,
    *,
    conversation_public_id: str,
    visitor_token: str | None,
    request: Request,
    locale: str | None = None,
    recording_consent: bool = False,
) -> dict[str, Any]:
    config = load_webchat_voice_runtime_config()
    if not config.human_call_enabled and not config.live_ai_voice_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="WebChat voice is disabled")
    conversation = _load_public_conversation(db, conversation_public_id)
    _validate_public_conversation_token(conversation, visitor_token)
    control = (
        db.query(ConversationControl)
        .filter(ConversationControl.conversation_id == conversation.id)
        .first()
    )
    if control is None or not control.country_code:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="conversation_scope_unavailable",
        )
    enforce_webchat_rate_limit(
        db,
        request,
        tenant_key=conversation.tenant_key,
        conversation_id=f"{conversation.public_id}:voice",
    )
    active = _active_session_for_conversation(db, conversation.id)
    if active is not None and _mark_missed_if_expired(db, session=active):
        db.flush()
        active = None
    if active is not None:
        value, ttl, identity = _issue_token(active, "visitor", "returning")
        return _serialize_session(
            active,
            participant_token=value,
            expires_in_seconds=ttl,
            participant_identity=identity,
        )

    now = utc_now()
    public_id = _new_voice_public_id()
    provider = _provider(config)
    room_name = _room_name(public_id, provider.provider_name)
    provider.create_room(room_name=room_name)
    ai_first = bool(config.live_ai_voice_enabled and config.routing_mode == "ai_first")
    try:
        session = WebchatVoiceSession(
            public_id=public_id,
            conversation_id=conversation.id,
            ticket_id=conversation.ticket_id,
            provider=provider.provider_name,
            provider_room_name=room_name,
            status="active" if ai_first else "ringing",
            mode="browser_ai" if ai_first else "browser_human",
            direction="inbound",
            locale=locale or None,
            recording_consent=bool(recording_consent),
            recording_status="disabled",
            transcript_status="active" if ai_first else "disabled",
            summary_status="pending",
            ai_agent_status="dispatching" if ai_first else None,
            ai_agent_started_at=now if ai_first else None,
            started_at=now,
            ringing_at=None if ai_first else now,
            active_at=now if ai_first else None,
            expires_at=now + timedelta(seconds=config.session_ttl_seconds),
            created_at=now,
            updated_at=now,
        )
        db.add(session)
        db.flush()
        value, ttl, identity = _issue_token(session, "visitor", "initial")
        db.add(
            WebchatVoiceParticipant(
                voice_session_id=session.id,
                participant_type="visitor",
                visitor_label=conversation.visitor_name or "Visitor",
                provider_identity=identity,
                status="invited",
                created_at=now,
            )
        )
        _write_voice_event(
            db,
            conversation_id=conversation.id,
            ticket_id=conversation.ticket_id,
            event_type="voice.session.created",
            payload={
                "voice_session_id": session.public_id,
                "provider": session.provider,
                "room_name": session.provider_room_name,
                "mode": session.mode,
            },
        )
        _emit_voice_observability(session, "voice.session.created")
        if ai_first:
            dispatch = provider.dispatch_agent(
                room_name=room_name,
                agent_name=config.livekit_agent_name or "nexus-voice-agent",
                metadata={
                    "schema": "nexus.livekit-agent-session.v1",
                    "voice_session_id": session.public_id,
                    "conversation_id": conversation.public_id,
                    "tenant_key": control.tenant_key,
                    "country_code": control.country_code,
                    "channel_key": control.channel_key,
                    "locale": locale,
                },
            )
            session.ai_agent_status = dispatch.provider_status
            _write_voice_event(
                db,
                conversation_id=conversation.id,
                ticket_id=conversation.ticket_id,
                event_type="voice.ai_agent.dispatched",
                payload={
                    "voice_session_id": session.public_id,
                    "provider_reference": dispatch.provider_reference,
                },
            )
            _emit_voice_observability(session, "voice.session.active")
        else:
            handoff = request_handoff(
                db,
                conversation=conversation,
                source="voice_call",
                trigger_type="voice_inbound",
                reason_code="customer_requested_voice_support",
                reason_text="Customer opened a live voice call.",
                recommended_agent_action="Answer the live voice call.",
                requested_by_actor_type="visitor",
            )
            session.handoff_request_id = handoff.id
            session.accepted_by_user_id = handoff.assigned_agent_id
            _write_voice_event(
                db,
                conversation_id=conversation.id,
                ticket_id=conversation.ticket_id,
                event_type="voice.session.ringing",
                payload={
                    "voice_session_id": session.public_id,
                    "handoff_request_id": handoff.id,
                },
            )
            _emit_voice_observability(session, "voice.session.ringing")
        db.flush()
        return _serialize_session(
            session,
            participant_token=value,
            expires_in_seconds=ttl,
            participant_identity=identity,
        )
    except Exception:
        try:
            provider.close_room(room_name=room_name)
        except Exception as compensation_exc:
            logger.warning(
                "voice_provider_room_create_compensation_failed",
                extra={
                    "voice_session_id": public_id,
                    "provider": provider.provider_name,
                    "room_name": room_name,
                    "error_type": type(compensation_exc).__name__,
                },
            )
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
    ensure_can_read_webcall_voice(current_user, db)
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ticket not found")
    ensure_ticket_visible(current_user, ticket, db)
    sessions = db.query(WebchatVoiceSession).filter(WebchatVoiceSession.ticket_id == ticket_id).order_by(WebchatVoiceSession.id.desc()).limit(20).all()
    return {"items": [_serialize_session(session) for session in sessions]}


def list_admin_voice_evidence(
    db: Session,
    *,
    voice_session_public_id: str,
    current_user: User,
    limit: int = 50,
) -> dict[str, Any]:
    ensure_can_read_webcall_voice(current_user, db)
    session, _conversation, _ticket = _visible_voice_session_context(
        db,
        voice_session_public_id=voice_session_public_id,
        current_user=current_user,
    )
    safe_limit = max(1, min(int(limit or 50), 100))
    segments = (
        db.query(WebchatVoiceTranscriptSegment)
        .filter(WebchatVoiceTranscriptSegment.voice_session_id == session.id)
        .order_by(WebchatVoiceTranscriptSegment.start_ms.asc().nullslast(), WebchatVoiceTranscriptSegment.id.asc())
        .limit(safe_limit)
        .all()
    )
    turns = (
        db.query(WebchatVoiceAITurn)
        .filter(WebchatVoiceAITurn.voice_session_id == session.id)
        .order_by(WebchatVoiceAITurn.turn_index.asc(), WebchatVoiceAITurn.id.asc())
        .limit(safe_limit)
        .all()
    )
    actions = (
        db.query(WebchatVoiceAIAction)
        .filter(WebchatVoiceAIAction.voice_session_id == session.id)
        .order_by(WebchatVoiceAIAction.id.asc())
        .limit(safe_limit)
        .all()
    )
    return {
        "ok": True,
        "ticket_id": session.ticket_id,
        "voice_session_id": session.public_id,
        "status": session.status,
        "provider": session.provider,
        "recording_status": session.recording_status,
        "transcript_status": session.transcript_status,
        "summary_status": session.summary_status,
        "ai_agent_status": session.ai_agent_status,
        "ai_turn_count": session.ai_turn_count,
        "transcript_segments": [
            {
                "id": segment.id,
                "segment_id": segment.segment_id,
                "speaker_type": segment.speaker_type,
                "speaker_label": segment.speaker_label,
                "language": segment.language,
                "is_final": segment.is_final,
                "start_ms": segment.start_ms,
                "end_ms": segment.end_ms,
                "text": segment.text_redacted or "[redaction pending]",
                "confidence": segment.confidence,
                "redaction_status": segment.redaction_status,
                "created_at": _serialize_dt(segment.created_at),
            }
            for segment in segments
        ],
        "ai_turns": [
            {
                "id": turn.id,
                "turn_index": turn.turn_index,
                "customer_text_redacted": turn.customer_text_redacted,
                "ai_response_text_redacted": turn.ai_response_text_redacted,
                "language": turn.language,
                "intent": turn.intent,
                "action": turn.action,
                "handoff_required": turn.handoff_required,
                "handoff_reason": turn.handoff_reason,
                "confidence": turn.confidence,
                "provider": turn.provider,
                "stt_provider": turn.stt_provider,
                "tts_provider": turn.tts_provider,
                "latency_ms": turn.latency_ms,
                "created_at": _serialize_dt(turn.created_at),
            }
            for turn in turns
        ],
        "ai_actions": [
            {
                "id": action.id,
                "turn_id": action.turn_id,
                "model_action": action.model_action,
                "nexus_decision": action.nexus_decision,
                "decision_reason": action.decision_reason,
                "speedaf_tool_name": action.speedaf_tool_name,
                "background_job_id": action.background_job_id,
                "tool_call_log_id": action.tool_call_log_id,
                "result_status": action.result_status,
                "created_at": _serialize_dt(action.created_at),
            }
            for action in actions
        ],
    }


def list_admin_voice_actions(
    db: Session,
    *,
    voice_session_public_id: str,
    current_user: User,
    limit: int = 20,
) -> dict[str, Any]:
    ensure_can_read_webcall_voice(current_user, db)
    session, _conversation, _ticket = _visible_voice_session_context(
        db,
        voice_session_public_id=voice_session_public_id,
        current_user=current_user,
    )
    safe_limit = max(1, min(int(limit or 20), 50))
    actions = (
        db.query(WebchatVoiceSessionAction)
        .filter(
            WebchatVoiceSessionAction.voice_session_id == session.id,
            WebchatVoiceSessionAction.action_type != "note",
        )
        .order_by(WebchatVoiceSessionAction.id.desc())
        .limit(safe_limit)
        .all()
    )
    return {"items": [_serialize_session_action(action) for action in actions]}


def record_admin_voice_action(
    db: Session,
    *,
    voice_session_public_id: str,
    current_user: User,
    action_type: str,
    target: str | None = None,
    digits: str | None = None,
    note: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    ensure_can_control_webcall_voice(current_user, db)
    session, _conversation, ticket = _visible_voice_session_context(
        db,
        voice_session_public_id=voice_session_public_id,
        current_user=current_user,
    )
    requested = (action_type or "").strip().lower()
    if requested not in CALL_CONTROL_ACTIONS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="unsupported webcall voice action")
    if session.status in TERMINAL_STATUSES:
        raise _conflict("voice session already closed")
    if requested in {"hold", "resume", "keypad", "transfer", "add_participant"} and session.status not in CALL_CONTROL_ACTIVE_STATUSES:
        raise _conflict("voice session action requires an active call")
    if requested == "keypad" and not digits:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="keypad digits are required")
    if requested in {"transfer", "add_participant"} and not target:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="action target is required")

    safe_payload = _safe_action_payload(requested, target=target, digits=digits, note=note)
    stable_material = json.dumps(
        {
            "session": session.public_id,
            "actor": current_user.id,
            "action": requested,
            "target": target,
            "digits_sha256": hashlib.sha256((digits or "").encode("utf-8")).hexdigest(),
            "note": note,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    key = (idempotency_key or "").strip()[:160] or (
        "voice-action:" + hashlib.sha256(stable_material.encode("utf-8")).hexdigest()
    )
    existing = (
        db.query(WebchatVoiceSessionAction)
        .filter(WebchatVoiceSessionAction.idempotency_key == key)
        .first()
    )
    if existing is not None:
        if existing.voice_session_id != session.id or existing.actor_user_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="voice action idempotency conflict")
        return {
            "ok": existing.status == "succeeded",
            "ticket_id": session.ticket_id,
            "voice_session_id": session.public_id,
            "action": _serialize_session_action(existing),
            "idempotent": True,
        }

    now = utc_now()
    action = WebchatVoiceSessionAction(
        voice_session_id=session.id,
        conversation_id=session.conversation_id,
        ticket_id=session.ticket_id,
        actor_user_id=current_user.id,
        action_type=requested,
        idempotency_key=key,
        status="processing",
        provider_status="executing",
        provider_reason="provider_command_started",
        attempt_count=1,
        last_attempt_at=now,
        payload_json=json.dumps(safe_payload, ensure_ascii=False, sort_keys=True),
        created_at=now,
    )
    db.add(action)
    db.flush()

    voice_configuration = None
    if session.called_number:
        voice_configuration = (
            db.query(VoiceChannelConfiguration)
            .join(ChannelAccount, ChannelAccount.id == VoiceChannelConfiguration.channel_account_id)
            .filter(
                ChannelAccount.provider == "voice",
                ChannelAccount.account_id == session.called_number,
                VoiceChannelConfiguration.enabled.is_(True),
            )
            .first()
        )
    provider = _provider_for_name(session.provider)
    try:
        provider_result = provider.execute_action(
            room_name=session.provider_room_name,
            action_type=requested,
            target=target,
            digits=digits,
            participant_identity=session.provider_call_id,
            outbound_trunk_id=(voice_configuration.outbound_trunk_id if voice_configuration else None),
            idempotency_key=key,
        )
        action.status = provider_result.status
        action.provider_status = provider_result.provider_status
        action.provider_reason = provider_result.provider_reason or "provider_command_completed"
        safe_payload.update(provider_result.safe_payload or {})
        if provider_result.provider_reference:
            safe_payload["provider_reference"] = provider_result.provider_reference
    except VoiceProviderError as exc:
        action.status = "failed"
        action.provider_status = "failed"
        action.provider_reason = str(exc)[:160] or "provider_command_failed"
        record_voice_provider_error(session.provider, requested)
    action.completed_at = utc_now()
    action.payload_json = json.dumps(safe_payload, ensure_ascii=False, sort_keys=True)

    event_payload = {
        "voice_session_id": session.public_id,
        "action_id": action.id,
        "action_type": requested,
        "status": action.status,
        "provider": session.provider,
        "provider_status": action.provider_status,
        "provider_reason": action.provider_reason,
        "payload": safe_payload,
    }
    ticket_event = None
    if ticket is not None:
        ticket_event = log_event(
            db,
            ticket_id=ticket.id,
            actor_id=current_user.id,
            event_type=EventType.field_updated,
            field_name="webcall.voice.action",
            new_value=requested,
            note="WebCall session action recorded",
            payload=event_payload,
        )
    webchat_event = _write_voice_event(
        db,
        conversation_id=session.conversation_id,
        ticket_id=session.ticket_id,
        event_type="voice.session.action_recorded",
        payload={**event_payload, "actor_user_id": current_user.id},
    )
    audit = log_admin_audit(
        db,
        actor_id=current_user.id,
        action=f"webcall.voice.action.{requested}",
        target_type="webchat_voice_session_action",
        target_id=action.id,
        old_value=None,
        new_value={**event_payload, "ticket_id": session.ticket_id},
    )
    action.ticket_event_id = ticket_event.id if ticket_event is not None else None
    action.webchat_event_id = webchat_event.id
    action.audit_id = audit.id
    session.updated_at = now
    db.flush()
    return {
        "ok": action.status == "succeeded",
        "ticket_id": session.ticket_id,
        "voice_session_id": session.public_id,
        "action": _serialize_session_action(action),
    }


def list_admin_incoming_voice_sessions(db: Session, *, current_user: User, status_filter: str = "ringing", limit: int = 50) -> dict[str, Any]:
    ensure_can_view_webcall_voice_queue(current_user, db)
    requested = (status_filter or "ringing").strip().lower()
    if requested in {"incoming", "ringing"}:
        statuses = {"created", "ringing"}
    elif requested in {"my_active", "mine"}:
        statuses = ACCEPTED_STATUSES
    elif requested in {"all_active", "live"}:
        statuses = ACCEPTED_STATUSES
    elif requested == "closed_recent":
        statuses = TERMINAL_STATUSES
    elif requested == "all":
        statuses = None
    elif requested in TERMINAL_STATUSES or requested in ACTIVE_STATUSES:
        statuses = {requested}
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid voice session status filter")

    safe_limit = max(1, min(int(limit or 50), 100))
    _cleanup_expired_ringing_sessions(db)
    query = (
        db.query(WebchatVoiceSession, Ticket, WebchatConversation)
        .outerjoin(Ticket, Ticket.id == WebchatVoiceSession.ticket_id)
        .join(WebchatConversation, WebchatConversation.id == WebchatVoiceSession.conversation_id)
        .filter(WebchatVoiceSession.mode != "internal_ai_demo")
    )
    if statuses is not None:
        query = query.filter(WebchatVoiceSession.status.in_(list(statuses)))
    if requested in {"my_active", "mine"}:
        query = query.filter(WebchatVoiceSession.accepted_by_user_id == current_user.id)
    if requested in {"closed_recent"}:
        query = query.order_by(WebchatVoiceSession.ended_at.desc().nullslast(), WebchatVoiceSession.id.desc())
    else:
        query = query.order_by(WebchatVoiceSession.id.desc())

    items: list[dict[str, Any]] = []
    for session, ticket, conversation in query.limit(safe_limit * 4).all():
        try:
            _ensure_voice_session_visible(db, current_user, session, conversation)
        except HTTPException as exc:
            if exc.status_code in {status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND}:
                continue
            raise
        if _mark_missed_if_expired(db, session=session):
            db.flush()
            if requested in {"incoming", "ringing"}:
                continue
            if statuses is not None and session.status not in statuses:
                continue
        items.append(_serialize_incoming_session(session, ticket, conversation))
        if len(items) >= safe_limit:
            break
    return {"items": items}


def save_admin_voice_note(
    db: Session,
    *,
    voice_session_public_id: str,
    current_user: User,
    body: str,
    source: str | None = None,
) -> dict[str, Any]:
    ensure_can_read_webcall_voice(current_user, db)
    ensure_can_write_internal_note(current_user, db)
    session, _conversation, ticket = _visible_voice_session_context(
        db,
        voice_session_public_id=voice_session_public_id,
        current_user=current_user,
    )
    normalized_body = (body or "").strip()
    if not normalized_body:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="voice note body is required")

    now = utc_now()
    source_value = (source or "webcall_operator_workbench").strip() or "webcall_operator_workbench"
    note_record = WebchatVoiceSessionAction(
        voice_session_id=session.id,
        conversation_id=session.conversation_id,
        ticket_id=session.ticket_id,
        actor_user_id=current_user.id,
        action_type="note",
        status="recorded",
        provider_status="not_applicable",
        provider_reason="internal_note",
        payload_json=json.dumps({"body": normalized_body, "source": source_value}, ensure_ascii=False),
        created_at=now,
    )
    db.add(note_record)
    db.flush()

    ticket_note = None
    ticket_event = None
    if ticket is not None:
        ticket_note = TicketInternalNote(
            ticket_id=ticket.id,
            author_id=current_user.id,
            body=normalized_body,
            created_at=now,
            updated_at=now,
        )
        db.add(ticket_note)
        db.flush()
        ticket_event = log_event(
            db,
            ticket_id=ticket.id,
            actor_id=current_user.id,
            event_type=EventType.internal_note_added,
            note="WebCall call note saved",
            payload={
                "voice_session_id": session.public_id,
                "note_id": ticket_note.id,
                "voice_note_id": note_record.id,
                "source": source_value,
                "provider": session.provider,
                "status": session.status,
            },
        )

    safe_payload = {
        "voice_session_id": session.public_id,
        "voice_note_id": note_record.id,
        "ticket_note_id": ticket_note.id if ticket_note is not None else None,
        "source": source_value,
        "provider": session.provider,
        "status": session.status,
        "body_length": len(normalized_body),
    }
    webchat_event = _write_voice_event(
        db,
        conversation_id=session.conversation_id,
        ticket_id=session.ticket_id,
        event_type="voice.session.note_saved",
        payload={**safe_payload, "author_id": current_user.id},
    )
    audit = log_admin_audit(
        db,
        actor_id=current_user.id,
        action="webcall.voice.note_saved",
        target_type="webchat_voice_session",
        target_id=session.id,
        old_value=None,
        new_value={**safe_payload, "ticket_id": session.ticket_id},
    )
    note_record.ticket_event_id = ticket_event.id if ticket_event is not None else None
    note_record.webchat_event_id = webchat_event.id
    note_record.audit_id = audit.id
    db.flush()
    return {
        "ok": True,
        "ticket_id": session.ticket_id,
        "voice_session_id": session.public_id,
        "note_id": ticket_note.id if ticket_note is not None else note_record.id,
        "ticket_event_id": ticket_event.id if ticket_event is not None else None,
        "webchat_event_id": webchat_event.id,
        "audit_id": audit.id,
        "created_at": note_record.created_at.isoformat(),
    }


def queue_speedaf_voice_callback(
    db: Session,
    *,
    voice_session_public_id: str,
    current_user: User,
    call_session_id: str | None,
    is_transferred_to_human: bool,
    action: dict[str, Any],
    request_id: str | None = None,
) -> dict[str, Any]:
    if os.getenv("SPEEDAF_VOICE_CALLBACK_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="speedaf_voice_callback_disabled")
    ensure_can_control_webcall_voice(current_user, db)
    ensure_can_send_speedaf_voice_callback(current_user, db)
    session, _conversation, ticket = _visible_voice_session_context(
        db,
        voice_session_public_id=voice_session_public_id,
        current_user=current_user,
    )
    if ticket is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="formal_ticket_required_for_voice_business_action",
        )
    ticket_id = ticket.id

    waybill_code = " ".join(str(action.get("waybillCode") or "").strip().split()).upper()
    action_name = " ".join(str(action.get("action") or "").strip().split())[:32]
    action_summary = " ".join(str(action.get("aiActionSummary") or "").strip().split())[:200]
    action_status = str(action.get("actionStatus") or "SUCCESS").strip().upper()
    error_code = " ".join(str(action.get("errorCode") or "").strip().split())[:80]
    if not waybill_code:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="speedaf_voice_callback_waybill_required")
    if not action_name:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="speedaf_voice_callback_action_required")
    if not action_summary:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="speedaf_voice_callback_summary_required")
    if action_status not in {"SUCCESS", "FAILED"}:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="speedaf_voice_callback_status_invalid")

    now = utc_now()
    action_time_input = str(action.get("actionTime") or "").strip()
    action_time = action_time_input or now.strftime("%Y-%m-%d %H:%M:%S")
    callback_action = {
        "waybillCode": waybill_code,
        "action": action_name,
        "actionTime": action_time,
        "aiActionSummary": action_summary,
        "actionStatus": action_status,
        "errorCode": error_code,
    }
    resolved_call_session_id = (call_session_id or session.public_id or str(session.id)).strip()
    dedupe_action = {**callback_action, "actionTime": action_time_input}
    dedupe_material = json.dumps(
        {
            "voice_session_id": session.id,
            "callSessionId": resolved_call_session_id,
            "isTransferredToHuman": bool(is_transferred_to_human),
            "action": dedupe_action,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    dedupe_key = f"speedaf-voice-callback:voice:{session.id}:payload:{hashlib.sha256(dedupe_material.encode('utf-8')).hexdigest()[:16]}"
    existing_job = find_recent_speedaf_voice_callback_job(db, dedupe_key=dedupe_key)
    if existing_job is not None:
        status_value = getattr(existing_job.status, "value", str(existing_job.status))
        response_status = "already_submitted" if status_value == "done" else "already_queued"
        return {
            "ok": True,
            "ticket_id": ticket_id,
            "voice_session_id": session.public_id,
            "status": response_status,
            "message": "Speedaf voice callback already submitted." if response_status == "already_submitted" else "Speedaf voice callback already queued.",
            "jobId": existing_job.id,
            "dedupeKey": dedupe_key,
            "ai_action_id": None,
        }
    job = enqueue_speedaf_voice_callback_job(
        db,
        ticket_id=ticket_id,
        voice_session_id=session.id,
        call_session_id=resolved_call_session_id,
        is_transferred_to_human=bool(is_transferred_to_human),
        action=callback_action,
        dedupe_key=dedupe_key,
        request_id=request_id,
    )
    ai_action = WebchatVoiceAIAction(
        voice_session_id=session.id,
        turn_id=None,
        model_action=action_name,
        nexus_decision="speedaf_voice_callback_queued",
        decision_reason="operator_confirmed_speedaf_voice_callback",
        speedaf_tool_name="speedaf.voice.callback",
        background_job_id=job.id,
        result_status="queued",
        created_at=now,
    )
    db.add(ai_action)
    db.flush()
    safe_payload = {
        "voice_session_id": session.public_id,
        "job_id": job.id,
        "ai_action_id": ai_action.id,
        "dedupe_key": dedupe_key,
        "call_session_id": {"redacted": True, "sha256": hashlib.sha256(resolved_call_session_id.encode("utf-8")).hexdigest()},
        "is_transferred_to_human": bool(is_transferred_to_human),
        "action": {
            **safe_waybill_payload(waybill_code),
            "action": action_name,
            "actionTime": action_time,
            "aiActionSummary": action_summary,
            "actionStatus": action_status,
            "errorCode": error_code,
        },
    }
    ticket_event = log_event(
        db,
        ticket_id=ticket_id,
        actor_id=current_user.id,
        event_type=EventType.field_updated,
        field_name="speedaf_voice_callback",
        new_value="queued",
        note="Speedaf voice callback queued.",
        payload=safe_payload,
    )
    webchat_event = _write_voice_event(
        db,
        conversation_id=session.conversation_id,
        ticket_id=ticket_id,
        event_type="voice.speedaf_callback.queued",
        payload={**safe_payload, "actor_user_id": current_user.id, "ticket_event_id": ticket_event.id},
    )
    audit = log_admin_audit(
        db,
        actor_id=current_user.id,
        action="speedaf.voice.callback.queued",
        target_type="webchat_voice_session",
        target_id=session.id,
        old_value=None,
        new_value={**safe_payload, "ticket_id": ticket_id, "webchat_event_id": webchat_event.id},
    )
    session.updated_at = now
    db.flush()
    return {
        "ok": True,
        "ticket_id": ticket_id,
        "voice_session_id": session.public_id,
        "status": "queued",
        "message": "Speedaf voice callback queued.",
        "jobId": job.id,
        "dedupeKey": dedupe_key,
        "ai_action_id": ai_action.id,
        "audit_id": audit.id,
    }


def accept_admin_voice_session(
    db: Session,
    *,
    voice_session_public_id: str,
    current_user: User,
) -> dict[str, Any]:
    ensure_can_accept_webcall_voice(current_user, db)
    session, conversation, _ticket = _visible_voice_session_context(
        db,
        voice_session_public_id=voice_session_public_id,
        current_user=current_user,
    )
    now = utc_now()
    if _mark_missed_if_expired(db, session=session, now=now):
        db.flush()
        raise _conflict(DETAIL_EXPIRED)
    if session.status in TERMINAL_STATUSES:
        raise _conflict(_closed_accept_detail(session.status))
    if session.mode in {"browser_ai", "sip_ai"} and session.ai_agent_status not in {None, "ended", "failed"}:
        raise _conflict("voice session is owned by the AI agent")

    request_row = (
        db.get(WebchatHandoffRequest, session.handoff_request_id)
        if session.handoff_request_id is not None
        else None
    )
    if request_row is None:
        request_row = request_handoff(
            db,
            conversation=conversation,
            source="voice_call",
            trigger_type="voice_accept",
            reason_code="voice_agent_required",
            requested_by_actor_type="agent",
            requested_by_user_id=current_user.id,
        )
        session.handoff_request_id = request_row.id
    if request_row.status == "requested":
        assign_handoff_to_agent(
            db,
            request_row=request_row,
            conversation=conversation,
            user=current_user,
            mode="voice_accept",
        )
        request_row = db.get(WebchatHandoffRequest, request_row.id)
    if request_row is None or request_row.status != "accepted":
        raise _conflict(DETAIL_NOT_ACCEPTABLE)
    if request_row.assigned_agent_id != current_user.id:
        raise _conflict(DETAIL_ALREADY_ACCEPTED_BY_OTHER)

    first_active = session.active_at is None
    session.status = "active"
    session.accepted_by_user_id = current_user.id
    session.accepted_at = session.accepted_at or now
    session.active_at = session.active_at or now
    session.wrap_up_expires_at = None
    session.updated_at = now
    value, ttl, identity = _issue_token(session, "agent", str(current_user.id))
    existing = (
        db.query(WebchatVoiceParticipant)
        .filter(
            WebchatVoiceParticipant.voice_session_id == session.id,
            WebchatVoiceParticipant.provider_identity == identity,
        )
        .first()
    )
    if existing is None:
        db.add(
            WebchatVoiceParticipant(
                voice_session_id=session.id,
                participant_type="agent",
                user_id=current_user.id,
                provider_identity=identity,
                status="invited",
                created_at=now,
            )
        )
    if first_active:
        for event_type in ("voice.session.accepted", "voice.session.active"):
            _write_voice_event(
                db,
                conversation_id=session.conversation_id,
                ticket_id=session.ticket_id,
                event_type=event_type,
                payload={
                    "voice_session_id": session.public_id,
                    "handoff_request_id": request_row.id,
                    "accepted_by_user_id": current_user.id,
                },
            )
            _emit_voice_observability(session, event_type)
    db.flush()
    return _serialize_session(
        session,
        participant_token=value,
        expires_in_seconds=ttl,
        participant_identity=identity,
    )


def reject_admin_voice_session(
    db: Session,
    *,
    voice_session_public_id: str,
    current_user: User,
    reason: str | None = None,
) -> dict[str, Any]:
    ensure_can_reject_webcall_voice(current_user, db)
    session, _conversation, _ticket = _visible_voice_session_context(
        db,
        voice_session_public_id=voice_session_public_id,
        current_user=current_user,
    )
    now = utc_now()
    if _mark_missed_if_expired(db, session=session, now=now):
        db.flush()
        raise _conflict(DETAIL_EXPIRED)
    if session.status in TERMINAL_STATUSES:
        return _serialize_session(session)
    decline_voice_handoff_offer(
        db,
        voice_session=session,
        user=current_user,
        reason_code="agent_declined_voice_offer",
        note=reason,
    )
    db.flush()
    return _serialize_session(session)


def end_admin_voice_session(
    db: Session,
    *,
    voice_session_public_id: str,
    current_user: User,
) -> dict[str, Any]:
    ensure_can_end_webcall_voice(current_user, db)
    session, _conversation, _ticket = _visible_voice_session_context(
        db,
        voice_session_public_id=voice_session_public_id,
        current_user=current_user,
    )
    _end_voice_session(db, session=session, ended_by_user_id=current_user.id)
    return {"ok": True, "status": session.status, "voice_session_id": session.public_id, "accepted_by_user_id": session.accepted_by_user_id}


def _close_provider_room_for_session(session: WebchatVoiceSession) -> None:
    try:
        _provider_for_name(session.provider).close_room(room_name=session.provider_room_name)
    except Exception as exc:
        record_voice_provider_error(session.provider, "close_room")
        logger.warning("voice_provider_room_close_skipped", extra={"voice_session_id": session.public_id, "provider": session.provider, "error_type": type(exc).__name__})


def _end_voice_session(
    db: Session,
    *,
    session: WebchatVoiceSession,
    ended_by_user_id: int | None,
) -> None:
    if session.status in TERMINAL_STATUSES:
        return
    previous_status = session.status
    now = utc_now()
    session.status = "ended" if previous_status in {"accepted", "active"} else "cancelled"
    session.ended_at = session.ended_at or now
    session.ended_by_user_id = ended_by_user_id
    if session.ai_agent_status and session.ai_agent_status not in {"ended", "failed"}:
        session.ai_agent_status = "ended"
        session.ai_agent_ended_at = now
    if session.status == "ended" and session.accepted_by_user_id is not None:
        state = get_or_create_agent_state(db, user_id=session.accepted_by_user_id, lock=True)
        wrap_up_seconds = max(0, int(state.voice_wrap_up_seconds or 0))
        session.wrap_up_expires_at = now + timedelta(seconds=wrap_up_seconds) if wrap_up_seconds else None
        if not wrap_up_seconds and session.handoff_request_id is not None:
            request_row = db.get(WebchatHandoffRequest, session.handoff_request_id)
            if request_row is not None and request_row.status == "accepted":
                request_row.status = "closed"
                request_row.closed_at = now
                request_row.lock_version += 1
                request_row.updated_at = now
    session.updated_at = now
    _close_provider_room_for_session(session)
    final_event_type = "voice.session.ended" if session.status == "ended" else "voice.session.cancelled"
    _write_voice_event(
        db,
        conversation_id=session.conversation_id,
        ticket_id=session.ticket_id,
        event_type=final_event_type,
        payload={
            "voice_session_id": session.public_id,
            "ended_by_user_id": ended_by_user_id,
            "wrap_up_expires_at": _serialize_dt(session.wrap_up_expires_at),
        },
    )
    _emit_voice_observability(session, final_event_type)
    record_voice_call_duration(
        session.provider,
        session.status,
        _voice_duration_seconds(session.started_at, session.ended_at),
    )
    record_voice_ringing_duration(
        session.provider,
        session.status,
        _voice_duration_seconds(session.ringing_at, session.accepted_at or session.ended_at),
    )
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
    body = "Voice call ended" if session.status == "ended" else "Voice call closed"
    if duration_seconds is not None:
        body = f"{body} · {duration_seconds}s"
    db.add(WebchatMessage(
        conversation_id=session.conversation_id,
        ticket_id=session.ticket_id,
        direction="system",
        body=body,
        body_text=body,
        message_type="voice_call",
        payload_json=json.dumps(_voice_evidence_payload(session), ensure_ascii=False),
        metadata_json=json.dumps({"generated_by": "system", "external_send": False}, ensure_ascii=False),
        client_message_id=client_message_id,
        delivery_status="sent",
        author_label="Voice",
    ))
