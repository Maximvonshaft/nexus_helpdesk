from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import timedelta, timezone
from typing import Any

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from ..models import ChannelAccount, Market, Tenant, Ticket, User
from ..models_agent_routing import ConversationControl
from ..settings import get_settings
from ..utils.time import utc_now
from ..voice_models import (
    VoiceChannelConfiguration,
    VoiceRoutingOffer,
    WebchatVoiceParticipant,
    WebchatVoiceSession,
)
from ..webchat_models import WebchatConversation, WebchatEvent, WebchatHandoffRequest
from ..webchat_voice_config import WebchatVoiceRuntimeConfig, load_webchat_voice_runtime_config
from .agent_routing_service import (
    accept_voice_offer,
    decline_voice_offer,
    expire_voice_offers,
    get_or_create_agent_state,
    request_handoff,
)
from .conversation_operator_service import ensure_conversation_visible
from .livekit_voice_provider import LiveKitVoiceProvider
from .mock_voice_provider import MockVoiceProvider
from .observability import (
    log_event as app_log_event,
    record_voice_call_duration,
    record_voice_provider_error,
    record_voice_ringing_duration,
    record_voice_session_event,
)
from .permissions import (
    ensure_can_accept_webcall_voice,
    ensure_can_end_webcall_voice,
    ensure_can_read_webcall_voice,
    ensure_can_reject_webcall_voice,
    ensure_can_view_webcall_voice_queue,
    ensure_ticket_visible,
)
from .voice_command_service import enqueue_voice_command, serialize_voice_command
from .voice_provider import VoiceProvider, VoiceProviderError
from .webchat_rate_limit import enforce_webchat_rate_limit

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = {"ended", "missed", "failed", "cancelled"}
OPEN_STATUSES = {"created", "ringing", "accepted", "active"}
DETAIL_EXPIRED = "voice session expired"


def _hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _aware_utc(value):
    if value is None:
        return None
    if getattr(value, "tzinfo", None) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _validate_visitor_token(
    conversation: WebchatConversation,
    token: str | None,
) -> None:
    if not token or _hash_token(token) != conversation.visitor_token_hash:
        raise HTTPException(status_code=403, detail="invalid webchat visitor token")
    expires_at = _aware_utc(conversation.visitor_token_expires_at)
    now = _aware_utc(utc_now())
    if expires_at is not None and now is not None and expires_at <= now:
        raise HTTPException(status_code=403, detail="invalid webchat visitor token")


def _provider_for_name(
    provider_name: str,
    config: WebchatVoiceRuntimeConfig | None = None,
) -> VoiceProvider:
    provider = str(provider_name or "").strip().lower()
    if provider == "mock":
        return MockVoiceProvider()
    if provider == "livekit":
        try:
            return LiveKitVoiceProvider.from_config(
                config or load_webchat_voice_runtime_config()
            )
        except VoiceProviderError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="voice_provider_unavailable",
            ) from exc
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="voice_provider_unavailable",
    )


def _new_public_id() -> str:
    return f"wv_{secrets.token_urlsafe(18)}"


def _room_name(public_id: str) -> str:
    return f"nexus_voice_{public_id}"[:160]


def _participant_identity(
    session: WebchatVoiceSession,
    participant_type: str,
    suffix: str,
) -> str:
    return f"{participant_type}_{session.public_id}_{suffix}"[:160]


def _write_event(
    db: Session,
    *,
    session: WebchatVoiceSession,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> WebchatEvent:
    row = WebchatEvent(
        conversation_id=session.conversation_id,
        ticket_id=session.ticket_id,
        event_type=event_type,
        payload_json=json.dumps(
            payload or {},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        ),
        created_at=utc_now(),
    )
    db.add(row)
    db.flush()
    return row


def _assigned_agent_id(
    db: Session,
    *,
    session: WebchatVoiceSession,
) -> int | None:
    if session.handoff_request_id is None:
        return None
    request_row = db.get(WebchatHandoffRequest, session.handoff_request_id)
    if request_row is None or request_row.status != "accepted":
        return None
    return request_row.assigned_agent_id


def _active_offer(
    db: Session,
    *,
    session: WebchatVoiceSession,
    agent_id: int | None = None,
) -> VoiceRoutingOffer | None:
    query = db.query(VoiceRoutingOffer).filter(
        VoiceRoutingOffer.voice_session_id == session.id,
        VoiceRoutingOffer.status == "offered",
        VoiceRoutingOffer.expires_at > utc_now(),
    )
    if agent_id is not None:
        query = query.filter(VoiceRoutingOffer.agent_id == agent_id)
    return query.order_by(VoiceRoutingOffer.id.desc()).first()


def _serialize_dt(value: Any) -> str | None:
    return value.isoformat() if value else None


def _duration(started_at: Any, ended_at: Any) -> int | None:
    start = _aware_utc(started_at)
    end = _aware_utc(ended_at)
    if not start or not end:
        return None
    return max(0, int((end - start).total_seconds()))


def serialize_voice_session(
    db: Session,
    *,
    session: WebchatVoiceSession,
    participant_token: str | None = None,
    expires_in_seconds: int | None = None,
    participant_identity: str | None = None,
    current_agent_id: int | None = None,
) -> dict[str, Any]:
    assigned_agent_id = _assigned_agent_id(db, session=session)
    offer = _active_offer(
        db,
        session=session,
        agent_id=current_agent_id,
    )
    config = load_webchat_voice_runtime_config()
    return {
        "ok": True,
        "voice_session_id": session.public_id,
        "status": session.status,
        "provider": session.provider,
        "media_plane": "livekit" if session.provider == "livekit" else "mock",
        "livekit_url": config.livekit_url if session.provider == "livekit" else None,
        "voice_page_url": f"/webchat/voice/{session.public_id}",
        "room_name": session.provider_room_name,
        "provider_room_name": session.provider_room_name,
        "participant_identity": participant_identity,
        "participant_token": participant_token,
        "expires_in_seconds": expires_in_seconds,
        "handoff_request_id": session.handoff_request_id,
        "accepted_by_user_id": assigned_agent_id,
        "ended_by_user_id": session.ended_by_user_id,
        "channel_account_id": session.channel_account_id,
        "direction": session.direction,
        "mode": session.mode,
        "ai_agent_status": session.ai_agent_status,
        "voice_offer": (
            {
                "id": offer.public_id,
                "expires_at": offer.expires_at.isoformat(),
            }
            if offer is not None
            else None
        ),
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
        "ringing_duration_seconds": _duration(
            session.ringing_at,
            session.accepted_at or session.ended_at,
        ),
        "talk_duration_seconds": _duration(
            session.accepted_at or session.active_at,
            session.ended_at,
        ),
        "total_duration_seconds": _duration(session.started_at, session.ended_at),
    }


def _emit_observability(
    db: Session,
    *,
    session: WebchatVoiceSession,
    event_type: str,
) -> None:
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
        assigned_agent_id=_assigned_agent_id(db, session=session),
        ended_by_user_id=session.ended_by_user_id,
    )


def _public_conversation(db: Session, public_id: str) -> WebchatConversation:
    row = (
        db.query(WebchatConversation)
        .filter(WebchatConversation.public_id == public_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="webchat conversation not found")
    return row


def _session_query(db: Session, public_id: str):
    query = db.query(WebchatVoiceSession).filter(
        WebchatVoiceSession.public_id == public_id
    )
    if db.bind and db.bind.dialect.name.startswith("postgresql"):
        query = query.with_for_update()
    return query


def load_voice_session(db: Session, public_id: str) -> WebchatVoiceSession:
    row = _session_query(db, public_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="webchat voice session not found")
    return row


def _conversation_control(
    db: Session,
    *,
    conversation: WebchatConversation,
) -> ConversationControl:
    row = (
        db.query(ConversationControl)
        .filter(ConversationControl.conversation_id == conversation.id)
        .first()
    )
    if row is None or not row.country_code:
        raise HTTPException(status_code=409, detail="conversation_scope_unavailable")
    return row


def _voice_channel(
    db: Session,
    *,
    control: ConversationControl,
    runtime: WebchatVoiceRuntimeConfig,
) -> tuple[ChannelAccount | None, VoiceChannelConfiguration | None]:
    query = (
        db.query(ChannelAccount, VoiceChannelConfiguration)
        .join(
            VoiceChannelConfiguration,
            VoiceChannelConfiguration.channel_account_id == ChannelAccount.id,
        )
        .join(Tenant, Tenant.id == ChannelAccount.tenant_id)
        .outerjoin(Market, Market.id == ChannelAccount.market_id)
        .filter(
            Tenant.tenant_key == control.tenant_key,
            Tenant.is_active.is_(True),
            ChannelAccount.provider == "voice",
            ChannelAccount.is_active.is_(True),
            VoiceChannelConfiguration.enabled.is_(True),
            ((Market.id.is_(None)) | (Market.country_code == control.country_code)),
        )
        .order_by(ChannelAccount.priority.asc(), ChannelAccount.id.asc())
    )
    result = query.first()
    if result is not None:
        return result
    settings = get_settings()
    if runtime.provider == "mock" and settings.app_env != "production":
        return None, None
    raise HTTPException(status_code=503, detail="voice_channel_unavailable")


def _issue_token(
    session: WebchatVoiceSession,
    participant_type: str,
    suffix: str,
) -> tuple[str, int, str]:
    runtime = load_webchat_voice_runtime_config()
    identity = _participant_identity(session, participant_type, suffix)
    token = _provider_for_name(session.provider, runtime).issue_participant_token(
        room_name=session.provider_room_name,
        participant_identity=identity,
        ttl_seconds=runtime.session_ttl_seconds,
    )
    return token.participant_token, token.expires_in_seconds, identity


def _active_session(
    db: Session,
    *,
    conversation_id: int,
) -> WebchatVoiceSession | None:
    return (
        db.query(WebchatVoiceSession)
        .filter(
            WebchatVoiceSession.conversation_id == conversation_id,
            WebchatVoiceSession.status.in_(sorted(OPEN_STATUSES)),
        )
        .order_by(WebchatVoiceSession.id.desc())
        .first()
    )


def _mark_terminal(
    db: Session,
    *,
    session: WebchatVoiceSession,
    status_value: str,
    ended_by_user_id: int | None,
    reason: str,
) -> None:
    if session.status in TERMINAL_STATUSES:
        return
    now = utc_now()
    previous_agent_id = _assigned_agent_id(db, session=session)
    session.status = status_value
    session.ended_at = session.ended_at or now
    session.ended_by_user_id = ended_by_user_id
    session.updated_at = now
    if session.ai_agent_status not in {None, "ended", "failed"}:
        session.ai_agent_status = "ended"
        session.ai_agent_ended_at = now
    if previous_agent_id is not None and status_value == "ended":
        state = get_or_create_agent_state(db, user_id=previous_agent_id, lock=True)
        wrap_up = max(0, int(state.voice_wrap_up_seconds or 0))
        session.wrap_up_expires_at = (
            now + timedelta(seconds=wrap_up) if wrap_up else None
        )
        if not wrap_up and session.handoff_request_id is not None:
            request_row = db.get(WebchatHandoffRequest, session.handoff_request_id)
            if request_row is not None and request_row.status == "accepted":
                request_row.status = "closed"
                request_row.closed_at = now
                request_row.lock_version += 1
                request_row.updated_at = now
    _write_event(
        db,
        session=session,
        event_type=f"voice.session.{status_value}",
        payload={
            "voice_session_id": session.public_id,
            "ended_by_user_id": ended_by_user_id,
            "reason": reason,
            "wrap_up_expires_at": _serialize_dt(session.wrap_up_expires_at),
        },
    )
    _emit_observability(
        db,
        session=session,
        event_type=f"voice.session.{status_value}",
    )
    record_voice_call_duration(
        session.provider,
        session.status,
        _duration(session.started_at, session.ended_at),
    )
    record_voice_ringing_duration(
        session.provider,
        session.status,
        _duration(session.ringing_at, session.accepted_at or session.ended_at),
    )


def _expire_session_if_needed(db: Session, *, session: WebchatVoiceSession) -> bool:
    if session.status not in {"created", "ringing"}:
        return False
    expires_at = _aware_utc(session.expires_at)
    now = _aware_utc(utc_now())
    if expires_at is None or now is None or expires_at > now:
        return False
    _mark_terminal(
        db,
        session=session,
        status_value="missed",
        ended_by_user_id=None,
        reason="session_expired",
    )
    try:
        _provider_for_name(session.provider).close_room(
            room_name=session.provider_room_name
        )
    except Exception as exc:
        record_voice_provider_error(session.provider, "close_room")
        logger.warning(
            "voice_provider_room_close_failed",
            extra={
                "voice_session_id": session.public_id,
                "error_type": type(exc).__name__,
            },
        )
    return True


def create_public_voice_session(
    db: Session,
    *,
    conversation_public_id: str,
    visitor_token: str | None,
    request: Request,
    locale: str | None = None,
    recording_consent: bool = False,
) -> dict[str, Any]:
    runtime = load_webchat_voice_runtime_config()
    if not runtime.human_call_enabled and not runtime.live_ai_voice_enabled:
        raise HTTPException(status_code=404, detail="WebChat voice is disabled")
    conversation = _public_conversation(db, conversation_public_id)
    _validate_visitor_token(conversation, visitor_token)
    control = _conversation_control(db, conversation=conversation)
    enforce_webchat_rate_limit(
        db,
        request,
        tenant_key=control.tenant_key,
        conversation_id=f"{conversation.public_id}:voice",
    )
    existing = _active_session(db, conversation_id=conversation.id)
    if existing is not None and _expire_session_if_needed(db, session=existing):
        existing = None
    if existing is not None:
        value, ttl, identity = _issue_token(existing, "visitor", "returning")
        return serialize_voice_session(
            db,
            session=existing,
            participant_token=value,
            expires_in_seconds=ttl,
            participant_identity=identity,
        )

    account, channel_config = _voice_channel(
        db,
        control=control,
        runtime=runtime,
    )
    provider = _provider_for_name(runtime.provider, runtime)
    now = utc_now()
    public_id = _new_public_id()
    room_name = _room_name(public_id)
    provider.create_room(room_name=room_name)
    routing_mode = (
        channel_config.routing_mode if channel_config is not None else runtime.routing_mode
    )
    ai_first = bool(runtime.live_ai_voice_enabled and routing_mode == "ai_first")
    recording_policy = (
        channel_config.recording_policy if channel_config is not None else "disabled"
    )
    transcription_policy = (
        channel_config.transcription_policy if channel_config is not None else "disabled"
    )
    recording_allowed = recording_policy == "always" or (
        recording_policy == "consent_required" and recording_consent
    )
    transcription_allowed = transcription_policy == "always" or (
        transcription_policy == "consent_required" and recording_consent
    )
    try:
        session = WebchatVoiceSession(
            public_id=public_id,
            conversation_id=conversation.id,
            ticket_id=conversation.ticket_id,
            channel_account_id=account.id if account is not None else None,
            provider=provider.provider_name,
            provider_room_name=room_name,
            status="active" if ai_first else "ringing",
            mode="browser_ai" if ai_first else "browser_human",
            direction="inbound",
            locale=locale or None,
            recording_consent=bool(recording_consent),
            recording_status="requested" if recording_allowed else "disabled",
            transcript_status="active" if transcription_allowed else "disabled",
            summary_status="pending",
            ai_agent_status="dispatching" if ai_first else None,
            ai_agent_started_at=now if ai_first else None,
            started_at=now,
            ringing_at=None if ai_first else now,
            active_at=now if ai_first else None,
            expires_at=now + timedelta(seconds=runtime.session_ttl_seconds),
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
                direction="inbound",
                status="invited",
                started_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        _write_event(
            db,
            session=session,
            event_type="voice.session.created",
            payload={
                "voice_session_id": session.public_id,
                "provider": session.provider,
                "channel_account_id": session.channel_account_id,
                "mode": session.mode,
            },
        )
        _emit_observability(db, session=session, event_type="voice.session.created")
        if ai_first:
            agent_name = (
                channel_config.ai_agent_name
                if channel_config is not None and channel_config.ai_agent_name
                else runtime.livekit_agent_name or "nexus-voice-agent"
            )
            dispatch = provider.dispatch_agent(
                room_name=room_name,
                agent_name=agent_name,
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
            _write_event(
                db,
                session=session,
                event_type="voice.ai_agent.dispatched",
                payload={
                    "voice_session_id": session.public_id,
                    "provider_reference": dispatch.provider_reference,
                },
            )
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
            _write_event(
                db,
                session=session,
                event_type="voice.session.ringing",
                payload={
                    "voice_session_id": session.public_id,
                    "handoff_request_id": handoff.id,
                },
            )
        db.flush()
        return serialize_voice_session(
            db,
            session=session,
            participant_token=value,
            expires_in_seconds=ttl,
            participant_identity=identity,
        )
    except Exception:
        try:
            provider.close_room(room_name=room_name)
        except Exception:
            logger.exception(
                "voice_room_compensation_failed",
                extra={"voice_session_id": public_id},
            )
        raise


def _visible_context(
    db: Session,
    *,
    public_id: str,
    current_user: User,
) -> tuple[WebchatVoiceSession, WebchatConversation, Ticket | None]:
    session = load_voice_session(db, public_id)
    conversation = db.get(WebchatConversation, session.conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="webchat conversation not found")
    ticket = None
    if session.ticket_id is not None:
        ticket = db.get(Ticket, session.ticket_id)
        if ticket is None:
            raise HTTPException(status_code=404, detail="ticket not found")
        ensure_ticket_visible(current_user, ticket, db)
    else:
        ensure_conversation_visible(db, conversation=conversation, user=current_user)
    return session, conversation, ticket


def end_public_voice_session(
    db: Session,
    *,
    conversation_public_id: str,
    voice_session_public_id: str,
    visitor_token: str | None,
) -> dict[str, Any]:
    conversation = _public_conversation(db, conversation_public_id)
    _validate_visitor_token(conversation, visitor_token)
    session = load_voice_session(db, voice_session_public_id)
    if session.conversation_id != conversation.id:
        raise HTTPException(status_code=404, detail="webchat voice session not found")
    _mark_terminal(
        db,
        session=session,
        status_value="ended" if session.status in {"accepted", "active"} else "cancelled",
        ended_by_user_id=None,
        reason="customer_hangup",
    )
    _provider_for_name(session.provider).close_room(
        room_name=session.provider_room_name
    )
    return serialize_voice_session(db, session=session)


def accept_admin_voice_session(
    db: Session,
    *,
    voice_session_public_id: str,
    current_user: User,
) -> dict[str, Any]:
    ensure_can_accept_webcall_voice(current_user, db)
    session, _conversation, _ticket = _visible_context(
        db,
        public_id=voice_session_public_id,
        current_user=current_user,
    )
    if _expire_session_if_needed(db, session=session):
        raise HTTPException(status_code=409, detail=DETAIL_EXPIRED)
    if session.status in TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail="voice session already closed")
    accept_voice_offer(db, voice_session=session, user=current_user)
    now = utc_now()
    session.status = "active"
    session.accepted_at = session.accepted_at or now
    session.active_at = session.active_at or now
    session.wrap_up_expires_at = None
    session.updated_at = now
    value, ttl, identity = _issue_token(session, "agent", str(current_user.id))
    leg = (
        db.query(WebchatVoiceParticipant)
        .filter(
            WebchatVoiceParticipant.voice_session_id == session.id,
            WebchatVoiceParticipant.user_id == current_user.id,
            WebchatVoiceParticipant.participant_type == "human",
        )
        .first()
    )
    if leg is not None:
        leg.provider_identity = identity
        leg.status = "invited"
        leg.started_at = leg.started_at or now
        leg.updated_at = now
    _write_event(
        db,
        session=session,
        event_type="voice.session.active",
        payload={
            "voice_session_id": session.public_id,
            "accepted_by_user_id": current_user.id,
        },
    )
    ai_leg = (
        db.query(WebchatVoiceParticipant)
        .filter(
            WebchatVoiceParticipant.voice_session_id == session.id,
            WebchatVoiceParticipant.participant_type == "ai",
            WebchatVoiceParticipant.status.notin_(["ended", "left", "failed"]),
        )
        .order_by(WebchatVoiceParticipant.id.desc())
        .first()
    )
    if ai_leg is not None:
        enqueue_voice_command(
            db,
            session=session,
            actor=current_user,
            action_type="remove_participant",
            target=ai_leg.provider_identity,
            note="canonical_human_handoff",
            idempotency_key=f"voice-ai-remove:{session.id}:{current_user.id}",
        )
    db.flush()
    return serialize_voice_session(
        db,
        session=session,
        participant_token=value,
        expires_in_seconds=ttl,
        participant_identity=identity,
        current_agent_id=current_user.id,
    )


def reject_admin_voice_session(
    db: Session,
    *,
    voice_session_public_id: str,
    current_user: User,
    reason: str | None = None,
) -> dict[str, Any]:
    ensure_can_reject_webcall_voice(current_user, db)
    session, _conversation, _ticket = _visible_context(
        db,
        public_id=voice_session_public_id,
        current_user=current_user,
    )
    if _expire_session_if_needed(db, session=session):
        raise HTTPException(status_code=409, detail=DETAIL_EXPIRED)
    if session.status in TERMINAL_STATUSES:
        return serialize_voice_session(db, session=session)
    decline_voice_offer(
        db,
        voice_session=session,
        user=current_user,
        reason_code="agent_declined_voice_offer",
        note=reason,
    )
    db.flush()
    return serialize_voice_session(
        db,
        session=session,
        current_agent_id=current_user.id,
    )


def end_admin_voice_session(
    db: Session,
    *,
    voice_session_public_id: str,
    current_user: User,
) -> dict[str, Any]:
    ensure_can_end_webcall_voice(current_user, db)
    session, _conversation, _ticket = _visible_context(
        db,
        public_id=voice_session_public_id,
        current_user=current_user,
    )
    if session.status in TERMINAL_STATUSES:
        return serialize_voice_session(db, session=session)
    command = enqueue_voice_command(
        db,
        session=session,
        actor=current_user,
        action_type="hangup",
        idempotency_key=f"voice-hangup:{session.id}:{current_user.id}",
    )
    return {
        "ok": True,
        "status": session.status,
        "voice_session_id": session.public_id,
        "command": serialize_voice_command(command),
    }


def list_admin_voice_sessions(
    db: Session,
    *,
    ticket_id: int,
    current_user: User,
) -> dict[str, Any]:
    ensure_can_read_webcall_voice(current_user, db)
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    ensure_ticket_visible(current_user, ticket, db)
    sessions = (
        db.query(WebchatVoiceSession)
        .filter(WebchatVoiceSession.ticket_id == ticket_id)
        .order_by(WebchatVoiceSession.id.desc())
        .limit(50)
        .all()
    )
    return {
        "items": [serialize_voice_session(db, session=row) for row in sessions]
    }


def _incoming_payload(
    db: Session,
    *,
    session: WebchatVoiceSession,
    ticket: Ticket | None,
    conversation: WebchatConversation,
    current_user: User,
) -> dict[str, Any]:
    payload = serialize_voice_session(
        db,
        session=session,
        current_agent_id=current_user.id,
    )
    payload.pop("participant_token", None)
    payload.pop("participant_identity", None)
    payload.update(
        {
            "ticket_id": ticket.id if ticket is not None else None,
            "ticket_no": ticket.ticket_no if ticket is not None else None,
            "ticket_title": ticket.title if ticket is not None else None,
            "conversation_id": conversation.public_id,
            "visitor_label": (
                conversation.visitor_name
                or conversation.visitor_email
                or conversation.visitor_phone
                or "Anonymous visitor"
            ),
            "origin": conversation.origin,
            "page_url": conversation.page_url,
        }
    )
    return payload


def list_admin_incoming_voice_sessions(
    db: Session,
    *,
    current_user: User,
    status_filter: str = "ringing",
    limit: int = 50,
) -> dict[str, Any]:
    ensure_can_view_webcall_voice_queue(current_user, db)
    expire_voice_offers(db, agent_id=current_user.id)
    requested = str(status_filter or "ringing").strip().lower()
    safe_limit = max(1, min(int(limit or 50), 100))
    query = (
        db.query(WebchatVoiceSession, Ticket, WebchatConversation)
        .outerjoin(Ticket, Ticket.id == WebchatVoiceSession.ticket_id)
        .join(
            WebchatConversation,
            WebchatConversation.id == WebchatVoiceSession.conversation_id,
        )
        .filter(WebchatVoiceSession.mode != "internal_ai_demo")
    )
    if requested in {"incoming", "ringing"}:
        query = query.join(
            VoiceRoutingOffer,
            VoiceRoutingOffer.voice_session_id == WebchatVoiceSession.id,
        ).filter(
            VoiceRoutingOffer.agent_id == current_user.id,
            VoiceRoutingOffer.status == "offered",
            VoiceRoutingOffer.expires_at > utc_now(),
            WebchatVoiceSession.status.in_(["created", "ringing"]),
        )
    elif requested in {"my_active", "mine"}:
        query = query.join(
            WebchatHandoffRequest,
            WebchatHandoffRequest.id == WebchatVoiceSession.handoff_request_id,
        ).filter(
            WebchatHandoffRequest.status == "accepted",
            WebchatHandoffRequest.assigned_agent_id == current_user.id,
            WebchatVoiceSession.status.in_(["accepted", "active"]),
        )
    elif requested in {"all_active", "live"}:
        query = query.filter(WebchatVoiceSession.status.in_(["accepted", "active"]))
    elif requested == "closed_recent":
        query = query.filter(WebchatVoiceSession.status.in_(sorted(TERMINAL_STATUSES)))
    elif requested != "all":
        allowed = TERMINAL_STATUSES | OPEN_STATUSES
        if requested not in allowed:
            raise HTTPException(status_code=400, detail="invalid voice session status filter")
        query = query.filter(WebchatVoiceSession.status == requested)

    order_column = (
        WebchatVoiceSession.ended_at.desc().nullslast()
        if requested == "closed_recent"
        else WebchatVoiceSession.id.desc()
    )
    items: list[dict[str, Any]] = []
    for session, ticket, conversation in query.order_by(order_column).limit(safe_limit * 4).all():
        try:
            if ticket is not None:
                ensure_ticket_visible(current_user, ticket, db)
            else:
                ensure_conversation_visible(
                    db,
                    conversation=conversation,
                    user=current_user,
                )
        except HTTPException as exc:
            if exc.status_code in {403, 404}:
                continue
            raise
        if _expire_session_if_needed(db, session=session):
            if requested in {"incoming", "ringing"}:
                continue
        items.append(
            _incoming_payload(
                db,
                session=session,
                ticket=ticket,
                conversation=conversation,
                current_user=current_user,
            )
        )
        if len(items) >= safe_limit:
            break
    return {"items": items}
