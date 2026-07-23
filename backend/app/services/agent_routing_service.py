from __future__ import annotations

import json
import secrets
from datetime import timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import and_, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Ticket, User
from ..models_agent_routing import ConversationControl, OperatorAgentState
from ..operator_models import OperatorQueueScopeGrant, OperatorTask
from ..utils.time import ensure_utc, utc_now
from ..voice_models import (
    VoiceChannelConfiguration,
    VoiceRoutingOffer,
    WebchatVoiceParticipant,
    WebchatVoiceSession,
)
from ..webchat_models import (
    WebchatConversation,
    WebchatEvent,
    WebchatHandoffDecision,
    WebchatHandoffRequest,
)
from .audit_service import log_admin_audit
from .conversation_first_service import ensure_conversation_control
from .operator_queue import create_operator_task
from .permissions import has_global_case_visibility
from .webchat_ai_turn_service import cancel_open_ai_turns_for_handoff

PRESENCE_STATUSES = {"offline", "online", "paused"}
CONVERSATION_OUTCOMES = {
    "ai_resolved",
    "human_resolved",
    "ticket_created",
    "customer_abandoned",
    "no_action_required",
    "unresolved",
}
HEARTBEAT_TTL_SECONDS = 90
DEFAULT_AGENT_CAPACITY = 3
MAX_AGENT_CAPACITY = 20
DEFAULT_VOICE_CAPACITY = 1
MAX_VOICE_CAPACITY = 5
DEFAULT_VOICE_WRAP_UP_SECONDS = 30
MAX_VOICE_WRAP_UP_SECONDS = 900
DEFAULT_VOICE_OFFER_TIMEOUT_SECONDS = 20
VOICE_CALL_OCCUPANCY_STATUSES = {"accepted", "active"}
VOICE_OPEN_SESSION_STATUSES = {"created", "ringing", "accepted", "active"}


def _lock(query, db: Session):
    if db.bind and db.bind.dialect.name.startswith("postgresql"):
        return query.with_for_update()
    return query


def _event(
    db: Session,
    *,
    conversation: WebchatConversation,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> WebchatEvent:
    row = WebchatEvent(
        conversation_id=conversation.id,
        ticket_id=conversation.ticket_id,
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


def get_or_create_agent_state(
    db: Session,
    *,
    user_id: int,
    lock: bool = False,
) -> OperatorAgentState:
    query = db.query(OperatorAgentState).filter(
        OperatorAgentState.user_id == user_id
    )
    if lock:
        query = _lock(query, db)
    row = query.first()
    if row is None:
        now = utc_now()
        row = OperatorAgentState(
            user_id=user_id,
            status="offline",
            max_concurrent_conversations=DEFAULT_AGENT_CAPACITY,
            voice_enabled=False,
            max_concurrent_voice_calls=DEFAULT_VOICE_CAPACITY,
            voice_wrap_up_seconds=DEFAULT_VOICE_WRAP_UP_SECONDS,
            status_changed_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.flush()
    return row


def heartbeat_is_fresh(
    row: OperatorAgentState,
    *,
    now=None,
) -> bool:
    heartbeat = ensure_utc(row.last_heartbeat_at)
    current = ensure_utc(now or utc_now())
    if heartbeat is None or current is None:
        return False
    return heartbeat >= current - timedelta(
        seconds=HEARTBEAT_TTL_SECONDS
    )


def _voice_session_for_conversation(
    db: Session,
    *,
    conversation_id: int,
) -> WebchatVoiceSession | None:
    return (
        db.query(WebchatVoiceSession)
        .filter(
            WebchatVoiceSession.conversation_id == conversation_id,
            WebchatVoiceSession.status.in_(
                sorted(VOICE_OPEN_SESSION_STATUSES)
            ),
        )
        .order_by(WebchatVoiceSession.id.desc())
        .first()
    )


def active_agent_load(db: Session, *, user_id: int) -> int:
    """Count accepted non-voice Handoffs owned by an operator."""

    voice_occupancy = (
        db.query(WebchatVoiceSession.id)
        .filter(
            WebchatVoiceSession.conversation_id
            == WebchatHandoffRequest.conversation_id,
            WebchatVoiceSession.status.in_(
                sorted(VOICE_OPEN_SESSION_STATUSES)
            ),
        )
        .exists()
    )
    return int(
        db.query(func.count(WebchatHandoffRequest.id))
        .join(
            WebchatConversation,
            WebchatConversation.id
            == WebchatHandoffRequest.conversation_id,
        )
        .filter(
            WebchatHandoffRequest.status == "accepted",
            WebchatHandoffRequest.assigned_agent_id == user_id,
            WebchatConversation.status == "open",
            ~voice_occupancy,
        )
        .scalar()
        or 0
    )


def active_voice_load(
    db: Session,
    *,
    user_id: int,
    now=None,
) -> int:
    """Count accepted calls and bounded after-call work."""

    current = ensure_utc(now or utc_now()) or utc_now()
    return int(
        db.query(func.count(WebchatVoiceSession.id))
        .join(
            WebchatHandoffRequest,
            WebchatHandoffRequest.id
            == WebchatVoiceSession.handoff_request_id,
        )
        .filter(
            WebchatHandoffRequest.status == "accepted",
            WebchatHandoffRequest.assigned_agent_id == user_id,
            (
                WebchatVoiceSession.status.in_(
                    sorted(VOICE_CALL_OCCUPANCY_STATUSES)
                )
                | (
                    WebchatVoiceSession.wrap_up_expires_at.isnot(None)
                    & (WebchatVoiceSession.wrap_up_expires_at > current)
                )
            ),
        )
        .scalar()
        or 0
    )


def reserved_voice_offer_count(
    db: Session,
    *,
    user_id: int,
    now=None,
) -> int:
    current = ensure_utc(now or utc_now()) or utc_now()
    return int(
        db.query(func.count(VoiceRoutingOffer.id))
        .filter(
            VoiceRoutingOffer.agent_id == user_id,
            VoiceRoutingOffer.status == "offered",
            VoiceRoutingOffer.expires_at > current,
        )
        .scalar()
        or 0
    )


def release_expired_voice_wrap_ups(
    db: Session,
    *,
    user_id: int | None = None,
    limit: int = 100,
) -> int:
    """Release expired after-call work without restarting the AI."""

    now = utc_now()
    query = db.query(WebchatVoiceSession).filter(
        WebchatVoiceSession.wrap_up_expires_at.isnot(None),
        WebchatVoiceSession.wrap_up_expires_at <= now,
    )
    if user_id is not None:
        query = query.join(
            WebchatHandoffRequest,
            WebchatHandoffRequest.id
            == WebchatVoiceSession.handoff_request_id,
        ).filter(
            WebchatHandoffRequest.assigned_agent_id == user_id
        )
    sessions = _lock(
        query.order_by(
            WebchatVoiceSession.wrap_up_expires_at.asc()
        ).limit(max(1, min(int(limit or 100), 500))),
        db,
    ).all()
    released = 0
    for session in sessions:
        session.wrap_up_expires_at = None
        request_row = (
            db.get(WebchatHandoffRequest, session.handoff_request_id)
            if session.handoff_request_id is not None
            else None
        )
        conversation = db.get(
            WebchatConversation,
            session.conversation_id,
        )
        previous_agent_id = (
            request_row.assigned_agent_id
            if request_row is not None
            else None
        )
        if request_row is not None and request_row.status == "accepted":
            request_row.status = "closed"
            request_row.closed_at = now
            request_row.decision_note = "voice_wrap_up_expired"
            request_row.lock_version += 1
            request_row.updated_at = now
        if (
            conversation is not None
            and conversation.active_agent_id == previous_agent_id
        ):
            conversation.active_agent_id = None
            conversation.current_handoff_request_id = None
            conversation.handoff_status = "closed"
            conversation.takeover_mode = None
            conversation.ai_suspended = True
            conversation.ai_suspended_reason = (
                "voice_follow_up_required"
            )
            conversation.updated_at = now
            _event(
                db,
                conversation=conversation,
                event_type="voice.wrap_up.expired",
                payload={
                    "voice_session_id": session.public_id,
                    "previous_agent_id": previous_agent_id,
                },
            )
        released += 1
    if released:
        db.flush()
    return released


def _state_payload(
    db: Session,
    row: OperatorAgentState,
) -> dict[str, Any]:
    load = active_agent_load(db, user_id=row.user_id)
    voice_load = active_voice_load(db, user_id=row.user_id)
    reserved_voice = reserved_voice_offer_count(
        db,
        user_id=row.user_id,
    )
    fresh = heartbeat_is_fresh(row)
    assignable = row.status == "online" and fresh
    available = (
        max(0, row.max_concurrent_conversations - load)
        if assignable
        else 0
    )
    voice_assignable = assignable and bool(row.voice_enabled)
    available_voice = (
        max(
            0,
            row.max_concurrent_voice_calls
            - voice_load
            - reserved_voice,
        )
        if voice_assignable
        else 0
    )
    return {
        "user_id": row.user_id,
        "status": row.status,
        "heartbeat_fresh": fresh,
        "assignable": assignable,
        "max_concurrent_conversations": (
            row.max_concurrent_conversations
        ),
        "active_conversations": load,
        "available_capacity": available,
        "voice_enabled": bool(row.voice_enabled),
        "voice_assignable": voice_assignable,
        "max_concurrent_voice_calls": row.max_concurrent_voice_calls,
        "active_voice_calls": voice_load,
        "reserved_voice_offers": reserved_voice,
        "available_voice_capacity": available_voice,
        "voice_wrap_up_seconds": row.voice_wrap_up_seconds,
        "last_heartbeat_at": (
            row.last_heartbeat_at.isoformat()
            if row.last_heartbeat_at
            else None
        ),
        "heartbeat_ttl_seconds": HEARTBEAT_TTL_SECONDS,
    }


def read_agent_state(
    db: Session,
    *,
    user_id: int,
) -> dict[str, Any]:
    expire_voice_offers(db, agent_id=user_id)
    release_expired_voice_wrap_ups(db, user_id=user_id)
    return _state_payload(
        db,
        get_or_create_agent_state(db, user_id=user_id),
    )


def set_agent_state(
    db: Session,
    *,
    user: User,
    presence_status: str,
    max_concurrent_conversations: int | None = None,
    voice_enabled: bool | None = None,
    max_concurrent_voice_calls: int | None = None,
    voice_wrap_up_seconds: int | None = None,
) -> dict[str, Any]:
    normalized = str(presence_status or "").strip().lower()
    if normalized not in PRESENCE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_agent_presence_status",
        )
    row = get_or_create_agent_state(
        db,
        user_id=user.id,
        lock=True,
    )
    old = _state_payload(db, row)
    now = utc_now()
    if max_concurrent_conversations is not None:
        capacity = int(max_concurrent_conversations)
        if not 1 <= capacity <= MAX_AGENT_CAPACITY:
            raise HTTPException(
                status_code=400,
                detail="invalid_agent_capacity",
            )
        row.max_concurrent_conversations = capacity
    if voice_enabled is not None:
        if (
            not voice_enabled
            and active_voice_load(db, user_id=user.id) > 0
        ):
            raise HTTPException(
                status_code=409,
                detail="agent_voice_disable_blocked_by_active_call",
            )
        row.voice_enabled = bool(voice_enabled)
    if max_concurrent_voice_calls is not None:
        voice_capacity = int(max_concurrent_voice_calls)
        if not 1 <= voice_capacity <= MAX_VOICE_CAPACITY:
            raise HTTPException(
                status_code=400,
                detail="invalid_agent_voice_capacity",
            )
        if voice_capacity < active_voice_load(db, user_id=user.id):
            raise HTTPException(
                status_code=409,
                detail="agent_voice_capacity_below_active_load",
            )
        row.max_concurrent_voice_calls = voice_capacity
    if voice_wrap_up_seconds is not None:
        wrap_up = int(voice_wrap_up_seconds)
        if not 0 <= wrap_up <= MAX_VOICE_WRAP_UP_SECONDS:
            raise HTTPException(
                status_code=400,
                detail="invalid_agent_voice_wrap_up",
            )
        row.voice_wrap_up_seconds = wrap_up
    if row.status != normalized:
        row.status = normalized
        row.status_changed_at = now
    row.last_heartbeat_at = (
        now if normalized in {"online", "paused"} else None
    )
    row.updated_at = now
    if normalized != "online" or not row.voice_enabled:
        _cancel_agent_voice_offers(
            db,
            agent_id=user.id,
            reason="agent_unavailable",
        )
    db.flush()
    if normalized == "online":
        release_expired_voice_wrap_ups(db, user_id=user.id)
        fill_agent_capacity(db, user=user)
    payload = _state_payload(db, row)
    log_admin_audit(
        db,
        actor_id=user.id,
        action="operator_agent_state.updated",
        target_type="operator_agent_state",
        target_id=row.id,
        old_value=old,
        new_value=payload,
    )
    return payload


def heartbeat_agent(
    db: Session,
    *,
    user: User,
) -> dict[str, Any]:
    row = get_or_create_agent_state(
        db,
        user_id=user.id,
        lock=True,
    )
    if row.status == "offline":
        return _state_payload(db, row)
    now = utc_now()
    row.last_heartbeat_at = now
    row.updated_at = now
    db.flush()
    expire_voice_offers(db, agent_id=user.id)
    release_expired_voice_wrap_ups(db, user_id=user.id)
    if row.status == "online":
        fill_agent_capacity(db, user=user)
    return _state_payload(db, row)


def _control_for_conversation(
    db: Session,
    conversation: WebchatConversation,
) -> ConversationControl:
    return ensure_conversation_control(db, conversation=conversation)


def _scope_grant_exists(
    db: Session,
    *,
    user: User,
    control: ConversationControl,
) -> bool:
    if not control.country_code:
        return False
    return bool(
        db.query(OperatorQueueScopeGrant.id)
        .filter(
            OperatorQueueScopeGrant.user_id == user.id,
            OperatorQueueScopeGrant.tenant_key == control.tenant_key,
            OperatorQueueScopeGrant.country_code
            == control.country_code,
            OperatorQueueScopeGrant.channel_key == control.channel_key,
            OperatorQueueScopeGrant.enabled.is_(True),
        )
        .first()
    )


def _operator_task(
    db: Session,
    *,
    conversation_id: int,
) -> OperatorTask | None:
    return (
        db.query(OperatorTask)
        .filter(
            OperatorTask.webchat_conversation_id == conversation_id,
            OperatorTask.task_type == "handoff",
            OperatorTask.status.notin_(
                [
                    "resolved",
                    "dropped",
                    "replayed",
                    "replay_failed",
                    "cancelled",
                ]
            ),
        )
        .order_by(OperatorTask.id.desc())
        .first()
    )


def _voice_offer_timeout_seconds(
    db: Session,
    *,
    session: WebchatVoiceSession,
) -> int:
    if session.channel_account_id is None:
        return DEFAULT_VOICE_OFFER_TIMEOUT_SECONDS
    config = (
        db.query(VoiceChannelConfiguration)
        .filter(
            VoiceChannelConfiguration.channel_account_id
            == session.channel_account_id
        )
        .first()
    )
    if config is None:
        return DEFAULT_VOICE_OFFER_TIMEOUT_SECONDS
    return max(
        5,
        min(
            int(
                config.offer_timeout_seconds
                or DEFAULT_VOICE_OFFER_TIMEOUT_SECONDS
            ),
            120,
        ),
    )


def _cancel_agent_voice_offers(
    db: Session,
    *,
    agent_id: int,
    reason: str,
) -> int:
    now = utc_now()
    offers = _lock(
        db.query(VoiceRoutingOffer).filter(
            VoiceRoutingOffer.agent_id == agent_id,
            VoiceRoutingOffer.status == "offered",
        ),
        db,
    ).all()
    affected_sessions: set[int] = set()
    for offer in offers:
        offer.status = "cancelled"
        offer.cancelled_at = now
        offer.decline_reason = reason[:240]
        offer.updated_at = now
        affected_sessions.add(offer.voice_session_id)
    db.flush()
    for session_id in affected_sessions:
        session = db.get(WebchatVoiceSession, session_id)
        if session is not None:
            create_next_voice_offer(db, voice_session=session)
    return len(offers)


def expire_voice_offers(
    db: Session,
    *,
    agent_id: int | None = None,
    voice_session_id: int | None = None,
    limit: int = 200,
) -> int:
    now = utc_now()
    query = db.query(VoiceRoutingOffer).filter(
        VoiceRoutingOffer.status == "offered",
        VoiceRoutingOffer.expires_at <= now,
    )
    if agent_id is not None:
        query = query.filter(VoiceRoutingOffer.agent_id == agent_id)
    if voice_session_id is not None:
        query = query.filter(
            VoiceRoutingOffer.voice_session_id == voice_session_id
        )
    offers = _lock(
        query.order_by(VoiceRoutingOffer.expires_at.asc()).limit(
            max(1, min(limit, 1000))
        ),
        db,
    ).all()
    affected_sessions: set[int] = set()
    for offer in offers:
        offer.status = "expired"
        offer.expired_at = now
        offer.updated_at = now
        affected_sessions.add(offer.voice_session_id)
        session = db.get(
            WebchatVoiceSession,
            offer.voice_session_id,
        )
        conversation = (
            db.get(WebchatConversation, session.conversation_id)
            if session
            else None
        )
        if conversation is not None:
            _event(
                db,
                conversation=conversation,
                event_type="voice.offer.expired",
                payload={
                    "voice_session_id": session.public_id,
                    "offer_id": offer.public_id,
                    "agent_id": offer.agent_id,
                },
            )
    db.flush()
    for session_id in affected_sessions:
        session = db.get(WebchatVoiceSession, session_id)
        if session is not None:
            create_next_voice_offer(db, voice_session=session)
    return len(offers)


def _agent_has_prior_voice_offer(
    db: Session,
    *,
    handoff_request_id: int,
    agent_id: int,
) -> bool:
    return bool(
        db.query(VoiceRoutingOffer.id)
        .filter(
            VoiceRoutingOffer.handoff_request_id
            == handoff_request_id,
            VoiceRoutingOffer.agent_id == agent_id,
            VoiceRoutingOffer.status.in_(
                ["offered", "accepted", "declined", "expired"]
            ),
        )
        .first()
    )


def _eligible_voice_agents(
    db: Session,
    *,
    request_row: WebchatHandoffRequest,
    control: ConversationControl,
) -> list[tuple[User, OperatorAgentState]]:
    if not control.country_code:
        return []
    candidates = (
        db.query(User, OperatorAgentState)
        .join(
            OperatorAgentState,
            OperatorAgentState.user_id == User.id,
        )
        .join(
            OperatorQueueScopeGrant,
            and_(
                OperatorQueueScopeGrant.user_id == User.id,
                OperatorQueueScopeGrant.tenant_key
                == control.tenant_key,
                OperatorQueueScopeGrant.country_code
                == control.country_code,
                OperatorQueueScopeGrant.channel_key
                == control.channel_key,
                OperatorQueueScopeGrant.enabled.is_(True),
            ),
        )
        .filter(
            User.is_active.is_(True),
            OperatorAgentState.status == "online",
            OperatorAgentState.voice_enabled.is_(True),
        )
        .order_by(
            OperatorAgentState.updated_at.asc(),
            User.id.asc(),
        )
        .all()
    )
    eligible: list[tuple[User, OperatorAgentState]] = []
    for user, state in candidates:
        if not heartbeat_is_fresh(state):
            continue
        if _agent_has_prior_voice_offer(
            db,
            handoff_request_id=request_row.id,
            agent_id=user.id,
        ):
            continue
        occupied = active_voice_load(db, user_id=user.id)
        reserved = reserved_voice_offer_count(
            db,
            user_id=user.id,
        )
        if occupied + reserved >= state.max_concurrent_voice_calls:
            continue
        eligible.append((user, state))
    return eligible


def create_next_voice_offer(
    db: Session,
    *,
    voice_session: WebchatVoiceSession,
) -> VoiceRoutingOffer | None:
    """Reserve one ringing agent without changing Handoff ownership."""

    if voice_session.status not in VOICE_OPEN_SESSION_STATUSES:
        return None
    request_row = (
        db.get(WebchatHandoffRequest, voice_session.handoff_request_id)
        if voice_session.handoff_request_id is not None
        else None
    )
    if request_row is None or request_row.status != "requested":
        return None
    now = utc_now()
    active_offer = (
        db.query(VoiceRoutingOffer)
        .filter(
            VoiceRoutingOffer.voice_session_id == voice_session.id,
            VoiceRoutingOffer.status == "offered",
            VoiceRoutingOffer.expires_at > now,
        )
        .first()
    )
    if active_offer is not None:
        return active_offer
    conversation = db.get(
        WebchatConversation,
        voice_session.conversation_id,
    )
    if conversation is None or conversation.status != "open":
        return None
    control = _control_for_conversation(db, conversation)
    candidates = _eligible_voice_agents(
        db,
        request_row=request_row,
        control=control,
    )
    if not candidates:
        return None
    user, _state = candidates[0]
    sequence = int(
        db.query(
            func.coalesce(func.max(VoiceRoutingOffer.sequence), 0)
        )
        .filter(
            VoiceRoutingOffer.voice_session_id == voice_session.id
        )
        .scalar()
        or 0
    ) + 1
    offer = VoiceRoutingOffer(
        public_id=f"vo_{secrets.token_urlsafe(18)}",
        voice_session_id=voice_session.id,
        handoff_request_id=request_row.id,
        agent_id=user.id,
        sequence=sequence,
        status="offered",
        offered_at=now,
        expires_at=now
        + timedelta(
            seconds=_voice_offer_timeout_seconds(
                db,
                session=voice_session,
            )
        ),
        created_at=now,
        updated_at=now,
    )
    try:
        with db.begin_nested():
            db.add(offer)
            db.flush()
    except IntegrityError:
        return (
            db.query(VoiceRoutingOffer)
            .filter(
                VoiceRoutingOffer.voice_session_id
                == voice_session.id,
                VoiceRoutingOffer.status == "offered",
                VoiceRoutingOffer.expires_at > utc_now(),
            )
            .first()
        )
    voice_session.status = "ringing"
    voice_session.ringing_at = voice_session.ringing_at or now
    voice_session.updated_at = now
    _event(
        db,
        conversation=conversation,
        event_type="voice.offer.created",
        payload={
            "voice_session_id": voice_session.public_id,
            "handoff_request_id": request_row.id,
            "offer_id": offer.public_id,
            "agent_id": user.id,
            "expires_at": offer.expires_at.isoformat(),
        },
    )
    log_admin_audit(
        db,
        actor_id=None,
        action="voice.offer.created",
        target_type="voice_routing_offer",
        target_id=offer.id,
        new_value={
            "voice_session_id": voice_session.public_id,
            "handoff_request_id": request_row.id,
            "agent_id": user.id,
            "expires_at": offer.expires_at.isoformat(),
        },
    )
    db.flush()
    return offer


def _eligible_text_request_for_agent(
    db: Session,
    *,
    user: User,
) -> tuple[
    WebchatHandoffRequest,
    WebchatConversation,
    ConversationControl,
] | None:
    declined_exists = (
        db.query(WebchatHandoffDecision.id)
        .filter(
            WebchatHandoffDecision.request_id
            == WebchatHandoffRequest.id,
            WebchatHandoffDecision.actor_id == user.id,
            WebchatHandoffDecision.decision == "declined",
        )
        .exists()
    )
    voice_exists = (
        db.query(WebchatVoiceSession.id)
        .filter(
            WebchatVoiceSession.conversation_id
            == WebchatHandoffRequest.conversation_id,
            WebchatVoiceSession.status.in_(
                sorted(VOICE_OPEN_SESSION_STATUSES)
            ),
        )
        .exists()
    )
    query = (
        db.query(
            WebchatHandoffRequest,
            WebchatConversation,
            ConversationControl,
        )
        .join(
            WebchatConversation,
            WebchatConversation.id
            == WebchatHandoffRequest.conversation_id,
        )
        .join(
            ConversationControl,
            ConversationControl.conversation_id
            == WebchatConversation.id,
        )
        .join(
            OperatorQueueScopeGrant,
            and_(
                OperatorQueueScopeGrant.user_id == user.id,
                OperatorQueueScopeGrant.tenant_key
                == ConversationControl.tenant_key,
                OperatorQueueScopeGrant.country_code
                == ConversationControl.country_code,
                OperatorQueueScopeGrant.channel_key
                == ConversationControl.channel_key,
                OperatorQueueScopeGrant.enabled.is_(True),
            ),
        )
        .filter(
            WebchatHandoffRequest.status == "requested",
            WebchatConversation.status == "open",
            ConversationControl.country_code.is_not(None),
            ~declined_exists,
            ~voice_exists,
        )
        .order_by(
            WebchatHandoffRequest.requested_at.asc(),
            WebchatHandoffRequest.id.asc(),
        )
        .limit(1)
    )
    return _lock(query, db).first()


def assign_handoff_to_agent(
    db: Session,
    *,
    request_row: WebchatHandoffRequest,
    conversation: WebchatConversation,
    user: User,
    mode: str = "automatic",
    voice_offer: VoiceRoutingOffer | None = None,
) -> dict[str, Any]:
    state = get_or_create_agent_state(
        db,
        user_id=user.id,
        lock=True,
    )
    if state.status != "online" or not heartbeat_is_fresh(state):
        raise HTTPException(
            status_code=409,
            detail="agent_not_available",
        )
    control = _control_for_conversation(db, conversation)
    if not _scope_grant_exists(db, user=user, control=control):
        raise HTTPException(
            status_code=403,
            detail="agent_scope_not_authorized",
        )

    locked_request = _lock(
        db.query(WebchatHandoffRequest).filter(
            WebchatHandoffRequest.id == request_row.id
        ),
        db,
    ).first()
    locked_conversation = _lock(
        db.query(WebchatConversation).filter(
            WebchatConversation.id == conversation.id
        ),
        db,
    ).first()
    if (
        locked_request is None
        or locked_conversation is None
        or locked_request.status != "requested"
    ):
        raise HTTPException(
            status_code=409,
            detail="handoff_not_waiting",
        )

    voice_session = _voice_session_for_conversation(
        db,
        conversation_id=locked_conversation.id,
    )
    now = utc_now()
    locked_offer: VoiceRoutingOffer | None = None
    if voice_session is not None:
        if not state.voice_enabled:
            raise HTTPException(
                status_code=409,
                detail="agent_voice_disabled",
            )
        offer_id = voice_offer.id if voice_offer is not None else None
        query = db.query(VoiceRoutingOffer).filter(
            VoiceRoutingOffer.voice_session_id == voice_session.id,
            VoiceRoutingOffer.agent_id == user.id,
            VoiceRoutingOffer.status == "offered",
        )
        if offer_id is not None:
            query = query.filter(VoiceRoutingOffer.id == offer_id)
        locked_offer = _lock(query, db).first()
        if locked_offer is None:
            raise HTTPException(
                status_code=409,
                detail="voice_offer_not_owned",
            )
        if locked_offer.expires_at <= now:
            raise HTTPException(
                status_code=409,
                detail="voice_offer_expired",
            )
        other_reservations = max(
            0,
            reserved_voice_offer_count(
                db,
                user_id=user.id,
                now=now,
            )
            - 1,
        )
        if (
            active_voice_load(db, user_id=user.id, now=now)
            + other_reservations
            >= state.max_concurrent_voice_calls
        ):
            raise HTTPException(
                status_code=409,
                detail="agent_voice_capacity_full",
            )
    elif (
        active_agent_load(db, user_id=user.id)
        >= state.max_concurrent_conversations
    ):
        raise HTTPException(
            status_code=409,
            detail="agent_capacity_full",
        )

    locked_request.status = "accepted"
    locked_request.accepted_by_user_id = user.id
    locked_request.assigned_agent_id = user.id
    locked_request.accepted_at = locked_request.accepted_at or now
    locked_request.lock_version += 1
    locked_request.updated_at = now
    locked_conversation.current_handoff_request_id = locked_request.id
    locked_conversation.handoff_status = "accepted"
    locked_conversation.active_agent_id = user.id
    locked_conversation.ai_suspended = True
    locked_conversation.ai_suspended_at = (
        locked_conversation.ai_suspended_at or now
    )
    locked_conversation.ai_suspended_by = user.id
    locked_conversation.ai_suspended_reason = "handoff_accepted"
    locked_conversation.takeover_mode = mode
    locked_conversation.updated_at = now

    if voice_session is not None and locked_offer is not None:
        locked_offer.status = "accepted"
        locked_offer.accepted_at = now
        locked_offer.updated_at = now
        voice_session.handoff_request_id = locked_request.id
        voice_session.status = "accepted"
        voice_session.accepted_at = now
        voice_session.updated_at = now
        db.query(VoiceRoutingOffer).filter(
            VoiceRoutingOffer.voice_session_id == voice_session.id,
            VoiceRoutingOffer.status == "offered",
            VoiceRoutingOffer.id != locked_offer.id,
        ).update(
            {
                VoiceRoutingOffer.status: "cancelled",
                VoiceRoutingOffer.cancelled_at: now,
                VoiceRoutingOffer.updated_at: now,
            },
            synchronize_session=False,
        )
        identity = f"agent:{user.id}"
        leg = (
            db.query(WebchatVoiceParticipant)
            .filter(
                WebchatVoiceParticipant.voice_session_id
                == voice_session.id,
                WebchatVoiceParticipant.user_id == user.id,
                WebchatVoiceParticipant.participant_type == "human",
            )
            .first()
        )
        if leg is None:
            db.add(
                WebchatVoiceParticipant(
                    voice_session_id=voice_session.id,
                    participant_type="human",
                    user_id=user.id,
                    provider_identity=identity,
                    direction="internal",
                    status="invited",
                    started_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )

    cancel_open_ai_turns_for_handoff(
        db,
        conversation=locked_conversation,
        actor_id=user.id,
        reason_code="handoff_accepted",
    )
    task = _operator_task(
        db,
        conversation_id=locked_conversation.id,
    )
    if task is not None:
        task.status = "assigned"
        task.assignee_id = user.id
        task.updated_at = now
    if locked_conversation.ticket_id is not None:
        ticket = db.get(Ticket, locked_conversation.ticket_id)
        if ticket is not None:
            ticket.assignee_id = user.id
            ticket.updated_at = now
    channel_kind = "voice" if voice_session is not None else "text"
    _event(
        db,
        conversation=locked_conversation,
        event_type="handoff.accepted",
        payload={
            "handoff_request_id": locked_request.id,
            "actor_id": user.id,
            "assignment_mode": mode,
            "channel_kind": channel_kind,
            "voice_offer_id": (
                locked_offer.public_id
                if locked_offer is not None
                else None
            ),
        },
    )
    log_admin_audit(
        db,
        actor_id=user.id,
        action="webchat_handoff.accepted",
        target_type="webchat_handoff_request",
        target_id=locked_request.id,
        new_value={
            "conversation_id": locked_conversation.id,
            "assigned_agent_id": user.id,
            "assignment_mode": mode,
            "channel_kind": channel_kind,
            "voice_offer_id": (
                locked_offer.public_id
                if locked_offer is not None
                else None
            ),
        },
    )
    db.flush()
    return serialize_handoff(
        db,
        request_row=locked_request,
        conversation=locked_conversation,
    )


def accept_voice_offer(
    db: Session,
    *,
    voice_session: WebchatVoiceSession,
    user: User,
) -> dict[str, Any]:
    if voice_session.handoff_request_id is None:
        raise HTTPException(
            status_code=409,
            detail="voice_handoff_missing",
        )
    request_row = db.get(
        WebchatHandoffRequest,
        voice_session.handoff_request_id,
    )
    conversation = db.get(
        WebchatConversation,
        voice_session.conversation_id,
    )
    if request_row is None or conversation is None:
        raise HTTPException(
            status_code=409,
            detail="voice_handoff_missing",
        )
    offer = (
        db.query(VoiceRoutingOffer)
        .filter(
            VoiceRoutingOffer.voice_session_id == voice_session.id,
            VoiceRoutingOffer.agent_id == user.id,
            VoiceRoutingOffer.status == "offered",
        )
        .first()
    )
    if offer is None:
        raise HTTPException(
            status_code=409,
            detail="voice_offer_not_owned",
        )
    return assign_handoff_to_agent(
        db,
        request_row=request_row,
        conversation=conversation,
        user=user,
        mode="voice_offer_accept",
        voice_offer=offer,
    )


def decline_voice_offer(
    db: Session,
    *,
    voice_session: WebchatVoiceSession,
    user: User,
    reason_code: str = "agent_declined_voice_offer",
    note: str | None = None,
) -> dict[str, Any]:
    if voice_session.handoff_request_id is None:
        raise HTTPException(
            status_code=409,
            detail="voice_handoff_missing",
        )
    request_row = db.get(
        WebchatHandoffRequest,
        voice_session.handoff_request_id,
    )
    conversation = db.get(
        WebchatConversation,
        voice_session.conversation_id,
    )
    if request_row is None or conversation is None:
        raise HTTPException(
            status_code=409,
            detail="voice_handoff_missing",
        )
    now = utc_now()
    offer = _lock(
        db.query(VoiceRoutingOffer).filter(
            VoiceRoutingOffer.voice_session_id == voice_session.id,
            VoiceRoutingOffer.agent_id == user.id,
            VoiceRoutingOffer.status == "offered",
        ),
        db,
    ).first()
    if offer is None:
        previous = (
            db.query(VoiceRoutingOffer)
            .filter(
                VoiceRoutingOffer.voice_session_id
                == voice_session.id,
                VoiceRoutingOffer.agent_id == user.id,
                VoiceRoutingOffer.status == "declined",
            )
            .order_by(VoiceRoutingOffer.id.desc())
            .first()
        )
        if previous is not None:
            return {
                "voice_session_id": voice_session.public_id,
                "offer_id": previous.public_id,
                "status": "declined",
                "idempotent": True,
            }
        raise HTTPException(
            status_code=409,
            detail="voice_offer_not_owned",
        )
    offer.status = "declined"
    offer.declined_at = now
    offer.decline_reason = (note or reason_code)[:240]
    offer.updated_at = now
    db.add(
        WebchatHandoffDecision(
            request_id=request_row.id,
            actor_id=user.id,
            decision="declined",
            reason_code=(
                reason_code or "agent_declined_voice_offer"
            )[:160],
            note=(note or "")[:1000] or None,
            created_at=now,
        )
    )
    voice_session.status = "ringing"
    voice_session.updated_at = now
    conversation.handoff_status = "requested"
    conversation.active_agent_id = None
    conversation.ai_suspended = True
    conversation.ai_suspended_reason = "voice_handoff_waiting"
    conversation.takeover_mode = None
    conversation.updated_at = now
    task = _operator_task(db, conversation_id=conversation.id)
    if task is not None:
        task.status = "pending"
        task.assignee_id = None
        task.updated_at = now
    _event(
        db,
        conversation=conversation,
        event_type="voice.offer.declined",
        payload={
            "voice_session_id": voice_session.public_id,
            "handoff_request_id": request_row.id,
            "offer_id": offer.public_id,
            "actor_id": user.id,
            "reason_code": reason_code,
        },
    )
    log_admin_audit(
        db,
        actor_id=user.id,
        action="voice.offer.declined",
        target_type="voice_routing_offer",
        target_id=offer.id,
        new_value={
            "voice_session_id": voice_session.public_id,
            "handoff_request_id": request_row.id,
            "reason_code": reason_code,
        },
    )
    db.flush()
    next_offer = create_next_voice_offer(
        db,
        voice_session=voice_session,
    )
    return {
        "voice_session_id": voice_session.public_id,
        "offer_id": offer.public_id,
        "status": "declined",
        "next_offer_id": (
            next_offer.public_id
            if next_offer is not None
            else None
        ),
    }


def fill_agent_capacity(
    db: Session,
    *,
    user: User,
) -> list[dict[str, Any]]:
    assigned: list[dict[str, Any]] = []
    state = get_or_create_agent_state(
        db,
        user_id=user.id,
        lock=True,
    )
    if state.status != "online" or not heartbeat_is_fresh(state):
        return assigned
    expire_voice_offers(db, agent_id=user.id)
    release_expired_voice_wrap_ups(db, user_id=user.id)

    while (
        active_agent_load(db, user_id=user.id)
        < state.max_concurrent_conversations
    ):
        candidate = _eligible_text_request_for_agent(db, user=user)
        if candidate is None:
            break
        request_row, conversation, _control = candidate
        try:
            assigned.append(
                assign_handoff_to_agent(
                    db,
                    request_row=request_row,
                    conversation=conversation,
                    user=user,
                    mode="automatic",
                )
            )
        except HTTPException as exc:
            if (
                exc.status_code == 409
                and exc.detail == "handoff_not_waiting"
            ):
                continue
            break

    if state.voice_enabled:
        voice_rows = (
            db.query(WebchatVoiceSession)
            .join(
                WebchatHandoffRequest,
                WebchatHandoffRequest.id
                == WebchatVoiceSession.handoff_request_id,
            )
            .filter(
                WebchatHandoffRequest.status == "requested",
                WebchatVoiceSession.status.in_(["created", "ringing"]),
            )
            .order_by(
                WebchatHandoffRequest.requested_at.asc(),
                WebchatVoiceSession.id.asc(),
            )
            .limit(100)
            .all()
        )
        for voice_session in voice_rows:
            create_next_voice_offer(
                db,
                voice_session=voice_session,
            )
    return assigned


def request_handoff(
    db: Session,
    *,
    conversation: WebchatConversation,
    source: str,
    trigger_type: str,
    reason_code: str | None = None,
    reason_text: str | None = None,
    recommended_agent_action: str | None = None,
    trigger_message_id: int | None = None,
    ai_turn_id: int | None = None,
    requested_by_actor_type: str = "system",
    requested_by_user_id: int | None = None,
) -> WebchatHandoffRequest:
    existing = _lock(
        db.query(WebchatHandoffRequest)
        .filter(
            WebchatHandoffRequest.conversation_id
            == conversation.id,
            WebchatHandoffRequest.status.in_(["requested", "accepted"]),
        )
        .order_by(WebchatHandoffRequest.id.desc()),
        db,
    ).first()
    now = utc_now()
    if existing is not None:
        if existing.status == "requested":
            existing.reason_code = existing.reason_code or reason_code
            existing.reason_text = existing.reason_text or reason_text
            existing.recommended_agent_action = (
                existing.recommended_agent_action
                or recommended_agent_action
            )
            existing.trigger_message_id = (
                existing.trigger_message_id or trigger_message_id
            )
            existing.ai_turn_id = existing.ai_turn_id or ai_turn_id
            existing.updated_at = now
            voice_session = _voice_session_for_conversation(
                db,
                conversation_id=conversation.id,
            )
            if voice_session is not None:
                voice_session.handoff_request_id = existing.id
                create_next_voice_offer(
                    db,
                    voice_session=voice_session,
                )
        return existing

    row = WebchatHandoffRequest(
        conversation_id=conversation.id,
        ticket_id=conversation.ticket_id,
        source=(source or "ai_auto")[:40],
        trigger_type=(trigger_type or "handoff_required")[:80],
        status="requested",
        reason_code=(reason_code or "human_review_required")[:160],
        reason_text=(reason_text or "")[:240] or None,
        recommended_agent_action=(
            recommended_agent_action or ""
        )[:1000]
        or None,
        trigger_message_id=trigger_message_id,
        ai_turn_id=ai_turn_id,
        requested_by_actor_type=(
            requested_by_actor_type or "system"
        )[:40],
        requested_by_user_id=requested_by_user_id,
        requested_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    voice_session = _voice_session_for_conversation(
        db,
        conversation_id=conversation.id,
    )
    if voice_session is not None:
        voice_session.handoff_request_id = row.id
        voice_session.status = "ringing"
        voice_session.ringing_at = voice_session.ringing_at or now
        voice_session.updated_at = now
    conversation.current_handoff_request_id = row.id
    conversation.handoff_status = "requested"
    conversation.active_agent_id = None
    conversation.ai_suspended = True
    conversation.ai_suspended_at = now
    conversation.ai_suspended_by = requested_by_user_id
    conversation.ai_suspended_reason = row.reason_code
    conversation.takeover_mode = None
    conversation.last_handoff_reason = row.reason_code
    conversation.updated_at = now
    cancel_open_ai_turns_for_handoff(
        db,
        conversation=conversation,
        actor_id=requested_by_user_id,
        reason_code="handoff_requested",
    )
    control = _control_for_conversation(db, conversation)
    task, _created = create_operator_task(
        db,
        source_type="webchat",
        source_id=str(conversation.id),
        ticket_id=conversation.ticket_id,
        webchat_conversation_id=conversation.id,
        task_type="handoff",
        reason_code=row.reason_code,
        payload={
            "handoff_request_id": row.id,
            "tenant_key": control.tenant_key,
            "country_code": control.country_code,
            "channel_key": control.channel_key,
            "visitor_name": conversation.visitor_name,
            "channel_kind": (
                "voice" if voice_session is not None else "text"
            ),
        },
    )
    task.status = "pending"
    task.assignee_id = None
    _event(
        db,
        conversation=conversation,
        event_type="handoff.requested",
        payload={
            "handoff_request_id": row.id,
            "source": row.source,
            "trigger_type": row.trigger_type,
            "reason_code": row.reason_code,
            "channel_kind": (
                "voice" if voice_session is not None else "text"
            ),
        },
    )
    log_admin_audit(
        db,
        actor_id=requested_by_user_id,
        action="webchat_handoff.requested",
        target_type="webchat_handoff_request",
        target_id=row.id,
        new_value={
            "conversation_id": conversation.id,
            "ticket_id": conversation.ticket_id,
            "reason": row.reason_code,
            "channel_kind": (
                "voice" if voice_session is not None else "text"
            ),
        },
    )
    db.flush()
    if voice_session is not None:
        create_next_voice_offer(
            db,
            voice_session=voice_session,
        )
    else:
        _auto_assign_text_request(
            db,
            request_row=row,
            conversation=conversation,
            control=control,
        )
    return row


def _auto_assign_text_request(
    db: Session,
    *,
    request_row: WebchatHandoffRequest,
    conversation: WebchatConversation,
    control: ConversationControl,
) -> None:
    candidates = (
        db.query(User, OperatorAgentState)
        .join(
            OperatorAgentState,
            OperatorAgentState.user_id == User.id,
        )
        .join(
            OperatorQueueScopeGrant,
            and_(
                OperatorQueueScopeGrant.user_id == User.id,
                OperatorQueueScopeGrant.tenant_key
                == control.tenant_key,
                OperatorQueueScopeGrant.country_code
                == control.country_code,
                OperatorQueueScopeGrant.channel_key
                == control.channel_key,
                OperatorQueueScopeGrant.enabled.is_(True),
            ),
        )
        .filter(
            User.is_active.is_(True),
            OperatorAgentState.status == "online",
        )
        .order_by(
            OperatorAgentState.updated_at.asc(),
            User.id.asc(),
        )
        .all()
    )
    for user, state in candidates:
        if request_row.status != "requested":
            return
        if not heartbeat_is_fresh(state):
            continue
        if (
            active_agent_load(db, user_id=user.id)
            >= state.max_concurrent_conversations
        ):
            continue
        assign_handoff_to_agent(
            db,
            request_row=request_row,
            conversation=conversation,
            user=user,
            mode="automatic",
        )
        return


def queue_position(
    db: Session,
    *,
    request_row: WebchatHandoffRequest,
) -> int | None:
    from .agent_availability_service import (
        queue_position as scoped_queue_position,
    )

    return scoped_queue_position(
        db,
        request_row=request_row,
    )


def availability_summary(
    db: Session,
    *,
    tenant_key: str,
    country_code: str | None,
    channel_key: str,
    request_row: WebchatHandoffRequest | None = None,
    conversation_id: int | None = None,
) -> dict[str, Any]:
    from .agent_availability_service import (
        availability_summary as scoped_availability_summary,
    )

    return scoped_availability_summary(
        db,
        tenant_key=tenant_key,
        country_code=country_code,
        channel_key=channel_key,
        request_row=request_row,
        conversation_id=conversation_id,
    )


def serialize_handoff(
    db: Session,
    *,
    request_row: WebchatHandoffRequest,
    conversation: WebchatConversation,
) -> dict[str, Any]:
    waiting_seconds = 0
    if request_row.requested_at:
        waiting_seconds = max(
            0,
            int(
                (
                    (ensure_utc(utc_now()) or utc_now())
                    - (
                        ensure_utc(request_row.requested_at)
                        or request_row.requested_at
                    )
                ).total_seconds()
            ),
        )
    voice_session = _voice_session_for_conversation(
        db,
        conversation_id=conversation.id,
    )
    active_offer = None
    if voice_session is not None:
        active_offer = (
            db.query(VoiceRoutingOffer)
            .filter(
                VoiceRoutingOffer.voice_session_id
                == voice_session.id,
                VoiceRoutingOffer.status == "offered",
                VoiceRoutingOffer.expires_at > utc_now(),
            )
            .first()
        )
    return {
        "id": request_row.id,
        "conversation_id": conversation.public_id,
        "webchat_conversation_id": conversation.id,
        "ticket_id": request_row.ticket_id,
        "status": request_row.status,
        "source": request_row.source,
        "trigger_type": request_row.trigger_type,
        "reason_code": request_row.reason_code,
        "reason_text": request_row.reason_text,
        "recommended_agent_action": (
            request_row.recommended_agent_action
        ),
        "assigned_agent_id": request_row.assigned_agent_id,
        "waiting_seconds": waiting_seconds,
        "queue_position": queue_position(
            db,
            request_row=request_row,
        ),
        "requested_at": (
            request_row.requested_at.isoformat()
            if request_row.requested_at
            else None
        ),
        "accepted_at": (
            request_row.accepted_at.isoformat()
            if request_row.accepted_at
            else None
        ),
        "handoff_status": conversation.handoff_status,
        "active_agent_id": conversation.active_agent_id,
        "ai_suspended": bool(conversation.ai_suspended),
        "voice_session_id": (
            voice_session.public_id
            if voice_session is not None
            else None
        ),
        "voice_offer": (
            {
                "id": active_offer.public_id,
                "agent_id": active_offer.agent_id,
                "expires_at": active_offer.expires_at.isoformat(),
            }
            if active_offer is not None
            else None
        ),
    }


def close_conversation(
    db: Session,
    *,
    conversation: WebchatConversation,
    user: User,
    outcome: str,
    note: str | None = None,
) -> dict[str, Any]:
    normalized = str(outcome or "").strip().lower()
    if normalized not in CONVERSATION_OUTCOMES:
        raise HTTPException(
            status_code=400,
            detail="invalid_conversation_outcome",
        )
    control = _control_for_conversation(db, conversation)
    if not _scope_grant_exists(db, user=user, control=control):
        raise HTTPException(
            status_code=403,
            detail="agent_scope_not_authorized",
        )
    if conversation.status != "open":
        return {
            "conversation_id": conversation.public_id,
            "status": conversation.status,
            "outcome": control.outcome,
            "idempotent": True,
        }
    if (
        conversation.active_agent_id not in {None, user.id}
        and not has_global_case_visibility(user, db)
    ):
        raise HTTPException(
            status_code=403,
            detail="conversation_owned_by_another_agent",
        )

    now = utc_now()
    request_row = None
    if conversation.current_handoff_request_id:
        request_row = _lock(
            db.query(WebchatHandoffRequest).filter(
                WebchatHandoffRequest.id
                == conversation.current_handoff_request_id
            ),
            db,
        ).first()
    if (
        request_row is not None
        and request_row.status in {"requested", "accepted"}
    ):
        request_row.status = "closed"
        request_row.closed_at = now
        request_row.decision_note = (note or "")[:1000] or None
        request_row.lock_version += 1
        request_row.updated_at = now
        db.query(VoiceRoutingOffer).filter(
            VoiceRoutingOffer.handoff_request_id == request_row.id,
            VoiceRoutingOffer.status == "offered",
        ).update(
            {
                VoiceRoutingOffer.status: "cancelled",
                VoiceRoutingOffer.cancelled_at: now,
                VoiceRoutingOffer.updated_at: now,
            },
            synchronize_session=False,
        )

    previous_agent_id = conversation.active_agent_id
    conversation.status = "closed"
    conversation.current_handoff_request_id = None
    conversation.handoff_status = "closed"
    conversation.active_agent_id = None
    conversation.ai_suspended = True
    conversation.ai_suspended_reason = "conversation_closed"
    conversation.takeover_mode = None
    conversation.updated_at = now
    control.outcome = normalized
    control.closed_at = now
    control.closed_by_user_id = user.id
    control.closure_note = (note or "")[:2000] or None
    control.updated_at = now
    task = _operator_task(db, conversation_id=conversation.id)
    if task is not None:
        task.status = "resolved"
        task.resolved_at = now
        task.updated_at = now
    _event(
        db,
        conversation=conversation,
        event_type="conversation.closed",
        payload={"outcome": normalized, "actor_id": user.id},
    )
    log_admin_audit(
        db,
        actor_id=user.id,
        action="conversation.closed",
        target_type="webchat_conversation",
        target_id=conversation.id,
        new_value={
            "outcome": normalized,
            "ticket_id": conversation.ticket_id,
        },
    )
    db.flush()
    if previous_agent_id is not None:
        previous_agent = db.get(User, previous_agent_id)
        if previous_agent is not None:
            fill_agent_capacity(db, user=previous_agent)
    return {
        "conversation_id": conversation.public_id,
        "status": conversation.status,
        "outcome": normalized,
        "ticket_id": conversation.ticket_id,
    }
