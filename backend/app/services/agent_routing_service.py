from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta
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
    WebchatHandoffRequest,
)
from .audit_service import log_admin_audit
from .conversation_first_service import ensure_conversation_control
from .handoff_routing_policy import (
    HandoffRoutingPolicy,
    HandoffRoutingPolicyError,
    active_decline_exists,
    build_handoff_routing_policy,
    classify_candidate_exhaustion,
    mark_routing_outcome,
    persist_handoff_routing_policy,
    record_routing_decline,
    request_policy,
    require_user_routing_eligible,
    routing_projection,
    start_next_routing_generation,
)
from .operator_queue import (
    HANDOFF_PROJECTION_SOURCE,
    create_webchat_handoff_task,
)
from .permissions import has_global_case_visibility, resolve_capabilities
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
TERMINAL_TASK_STATUSES = {
    "resolved",
    "dropped",
    "replayed",
    "replay_failed",
    "cancelled",
}


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
    now: datetime | None = None,
) -> bool:
    heartbeat = ensure_utc(row.last_heartbeat_at)
    current = ensure_utc(now or utc_now())
    if heartbeat is None or current is None:
        return False
    return heartbeat >= current - timedelta(seconds=HEARTBEAT_TTL_SECONDS)


def _voice_session_for_conversation(
    db: Session,
    *,
    conversation_id: int,
) -> WebchatVoiceSession | None:
    return (
        db.query(WebchatVoiceSession)
        .filter(
            WebchatVoiceSession.conversation_id == conversation_id,
            WebchatVoiceSession.status.in_(sorted(VOICE_OPEN_SESSION_STATUSES)),
        )
        .order_by(WebchatVoiceSession.id.desc())
        .first()
    )


def active_agent_load(db: Session, *, user_id: int) -> int:
    """Count accepted non-voice Handoffs owned by one operator."""

    voice_occupancy = (
        db.query(WebchatVoiceSession.id)
        .filter(
            WebchatVoiceSession.conversation_id
            == WebchatHandoffRequest.conversation_id,
            WebchatVoiceSession.status.in_(sorted(VOICE_OPEN_SESSION_STATUSES)),
        )
        .exists()
    )
    return int(
        db.query(func.count(WebchatHandoffRequest.id))
        .join(
            WebchatConversation,
            WebchatConversation.id == WebchatHandoffRequest.conversation_id,
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
    now: datetime | None = None,
) -> int:
    """Count accepted calls and bounded after-call work."""

    current = ensure_utc(now or utc_now()) or utc_now()
    return int(
        db.query(func.count(WebchatVoiceSession.id))
        .join(
            WebchatHandoffRequest,
            WebchatHandoffRequest.id == WebchatVoiceSession.handoff_request_id,
        )
        .filter(
            WebchatHandoffRequest.status == "accepted",
            WebchatHandoffRequest.assigned_agent_id == user_id,
            (
                WebchatVoiceSession.status.in_(sorted(VOICE_CALL_OCCUPANCY_STATUSES))
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
    now: datetime | None = None,
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
            WebchatHandoffRequest.id == WebchatVoiceSession.handoff_request_id,
        ).filter(WebchatHandoffRequest.assigned_agent_id == user_id)
    sessions = _lock(
        query.order_by(WebchatVoiceSession.wrap_up_expires_at.asc()).limit(
            max(1, min(int(limit or 100), 500))
        ),
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
        conversation = db.get(WebchatConversation, session.conversation_id)
        previous_agent_id = (
            request_row.assigned_agent_id if request_row is not None else None
        )
        if request_row is not None and request_row.status == "accepted":
            request_row.status = "closed"
            request_row.closed_at = now
            request_row.decision_note = "voice_wrap_up_expired"
            request_row.routing_outcome = "fallback_selected"
            request_row.routing_reason_code = "voice_wrap_up_expired"
            request_row.routing_fallback_action = "follow_up_required"
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
            conversation.ai_suspended_reason = "voice_follow_up_required"
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
    reserved_voice = reserved_voice_offer_count(db, user_id=row.user_id)
    fresh = heartbeat_is_fresh(row)
    assignable = row.status == "online" and fresh
    available = (
        max(0, row.max_concurrent_conversations - load) if assignable else 0
    )
    voice_assignable = assignable and bool(row.voice_enabled)
    available_voice = (
        max(
            0,
            row.max_concurrent_voice_calls - voice_load - reserved_voice,
        )
        if voice_assignable
        else 0
    )
    return {
        "user_id": row.user_id,
        "status": row.status,
        "heartbeat_fresh": fresh,
        "assignable": assignable,
        "max_concurrent_conversations": row.max_concurrent_conversations,
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
            row.last_heartbeat_at.isoformat() if row.last_heartbeat_at else None
        ),
        "heartbeat_ttl_seconds": HEARTBEAT_TTL_SECONDS,
    }


def read_agent_state(db: Session, *, user_id: int) -> dict[str, Any]:
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
        raise HTTPException(status_code=400, detail="invalid_agent_presence_status")
    row = get_or_create_agent_state(db, user_id=user.id, lock=True)
    old = _state_payload(db, row)
    now = utc_now()
    if max_concurrent_conversations is not None:
        capacity = int(max_concurrent_conversations)
        if not 1 <= capacity <= MAX_AGENT_CAPACITY:
            raise HTTPException(status_code=400, detail="invalid_agent_capacity")
        row.max_concurrent_conversations = capacity
    if voice_enabled is not None:
        if not voice_enabled and active_voice_load(db, user_id=user.id) > 0:
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
    row.last_heartbeat_at = now if normalized in {"online", "paused"} else None
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


def heartbeat_agent(db: Session, *, user: User) -> dict[str, Any]:
    row = get_or_create_agent_state(db, user_id=user.id, lock=True)
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
            OperatorQueueScopeGrant.country_code == control.country_code,
            OperatorQueueScopeGrant.channel_key == control.channel_key,
            OperatorQueueScopeGrant.enabled.is_(True),
        )
        .first()
    )


def _operator_task(
    db: Session,
    *,
    request_id: int,
) -> OperatorTask | None:
    return (
        db.query(OperatorTask)
        .filter(
            OperatorTask.source_type == HANDOFF_PROJECTION_SOURCE,
            OperatorTask.source_id == str(request_id),
            OperatorTask.task_type == "handoff",
            OperatorTask.status.notin_(list(TERMINAL_TASK_STATUSES)),
        )
        .order_by(OperatorTask.id.desc())
        .first()
    )


def _ensure_operator_task(
    db: Session,
    *,
    request_row: WebchatHandoffRequest,
    conversation: WebchatConversation,
    policy: HandoffRoutingPolicy,
) -> OperatorTask:
    task = _operator_task(db, request_id=request_row.id)
    if task is None:
        task = create_webchat_handoff_task(
            db,
            conversation=conversation,
            reason_code=request_row.reason_code or "human_review_required",
            payload={
                "handoff_request_id": request_row.id,
                "routing_generation": request_row.routing_generation,
                "routing_policy_sha256": policy.policy_sha256,
            },
        )
    task.priority = policy.priority
    task.source_version = request_row.lock_version
    task.updated_at = utc_now()
    return task


def _ensure_request_policy(
    db: Session,
    *,
    request_row: WebchatHandoffRequest,
    conversation: WebchatConversation,
) -> HandoffRoutingPolicy:
    ticket = (
        db.get(Ticket, conversation.ticket_id)
        if conversation.ticket_id is not None
        else None
    )
    current = build_handoff_routing_policy(
        db,
        conversation=conversation,
        ticket=ticket,
    )
    stored: HandoffRoutingPolicy | None = None
    if request_row.routing_policy_json and request_row.routing_policy_sha256:
        stored = request_policy(request_row)
    if stored is None:
        persist_handoff_routing_policy(request_row, current)
        return current
    if stored.policy_sha256 == current.policy_sha256:
        return stored
    if request_row.status != "requested":
        raise HandoffRoutingPolicyError("handoff_routing_policy_stale")
    start_next_routing_generation(
        request_row,
        reason_code="scenario_or_priority_contract_changed",
    )
    persist_handoff_routing_policy(request_row, current)
    _event(
        db,
        conversation=conversation,
        event_type="handoff.routing_policy_refreshed",
        payload={
            "handoff_request_id": request_row.id,
            "routing_generation": request_row.routing_generation,
            "routing_policy_sha256": current.policy_sha256,
        },
    )
    return current


def _maybe_start_due_retry(
    db: Session,
    *,
    request_row: WebchatHandoffRequest,
    conversation: WebchatConversation,
) -> bool:
    retry_at = ensure_utc(request_row.routing_retry_at)
    now = ensure_utc(utc_now()) or utc_now()
    if retry_at is None or retry_at > now:
        return False
    start_next_routing_generation(
        request_row,
        reason_code="scheduled_retry_started",
    )
    _event(
        db,
        conversation=conversation,
        event_type="handoff.routing_retry_started",
        payload={
            "handoff_request_id": request_row.id,
            "routing_generation": request_row.routing_generation,
        },
    )
    return True


def _routing_event(
    db: Session,
    *,
    conversation: WebchatConversation,
    request_row: WebchatHandoffRequest,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    safe = {
        "handoff_request_id": request_row.id,
        "routing_generation": request_row.routing_generation,
        **routing_projection(request_row),
        **payload,
    }
    _event(
        db,
        conversation=conversation,
        event_type=event_type,
        payload=safe,
    )
    log_admin_audit(
        db,
        actor_id=None,
        action=event_type,
        target_type="webchat_handoff_request",
        target_id=request_row.id,
        new_value=safe,
    )


def _mark_candidate_exhaustion(
    db: Session,
    *,
    request_row: WebchatHandoffRequest,
    conversation: WebchatConversation,
    scoped_agents: int,
    skilled_agents: int,
    available_agents: int,
    declined_agents: int,
) -> None:
    outcome, reason, owner, retry_at = classify_candidate_exhaustion(
        scoped_agents=scoped_agents,
        skilled_agents=skilled_agents,
        available_agents=available_agents,
        declined_agents=declined_agents,
    )
    fallback_action = (
        "supervisor_review" if outcome == "skill_unavailable" else "scheduled_retry"
    )
    mark_routing_outcome(
        request_row,
        outcome=outcome,
        reason_code=reason,
        owner=owner,
        retry_at=retry_at,
        fallback_action=fallback_action,
    )
    _routing_event(
        db,
        conversation=conversation,
        request_row=request_row,
        event_type="handoff.routing_exhausted",
        payload={
            "scoped_agents": scoped_agents,
            "skilled_agents": skilled_agents,
            "available_agents": available_agents,
            "declined_agents": declined_agents,
        },
    )
    task = _operator_task(db, request_id=request_row.id)
    if task is not None:
        task.reason_code = reason[:160]
        task.updated_at = utc_now()


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
        request_row = db.get(WebchatHandoffRequest, offer.handoff_request_id)
        if request_row is not None:
            record_routing_decline(
                db,
                request_row=request_row,
                user_id=agent_id,
                reason_code=reason,
                note=None,
            )
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
        request_row = db.get(WebchatHandoffRequest, offer.handoff_request_id)
        if request_row is not None:
            record_routing_decline(
                db,
                request_row=request_row,
                user_id=offer.agent_id,
                reason_code="voice_offer_expired",
                note=None,
            )
        affected_sessions.add(offer.voice_session_id)
        session = db.get(WebchatVoiceSession, offer.voice_session_id)
        conversation = (
            db.get(WebchatConversation, session.conversation_id)
            if session is not None
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
                    "routing_generation": (
                        request_row.routing_generation
                        if request_row is not None
                        else None
                    ),
                },
            )
    db.flush()
    for session_id in affected_sessions:
        session = db.get(WebchatVoiceSession, session_id)
        if session is not None:
            create_next_voice_offer(db, voice_session=session)
    return len(offers)


def _scoped_agent_rows(
    db: Session,
    *,
    control: ConversationControl,
) -> list[tuple[User, OperatorAgentState]]:
    if not control.country_code:
        return []
    return (
        db.query(User, OperatorAgentState)
        .join(
            OperatorAgentState,
            OperatorAgentState.user_id == User.id,
        )
        .join(
            OperatorQueueScopeGrant,
            and_(
                OperatorQueueScopeGrant.user_id == User.id,
                OperatorQueueScopeGrant.tenant_key == control.tenant_key,
                OperatorQueueScopeGrant.country_code == control.country_code,
                OperatorQueueScopeGrant.channel_key == control.channel_key,
                OperatorQueueScopeGrant.enabled.is_(True),
            ),
        )
        .filter(User.is_active.is_(True))
        .order_by(OperatorAgentState.updated_at.asc(), User.id.asc())
        .all()
    )


def _candidate_rows(
    db: Session,
    *,
    request_row: WebchatHandoffRequest,
    conversation: WebchatConversation,
    control: ConversationControl,
    voice: bool,
) -> tuple[list[tuple[User, OperatorAgentState]], dict[str, int]]:
    policy = _ensure_request_policy(
        db,
        request_row=request_row,
        conversation=conversation,
    )
    scoped_rows = _scoped_agent_rows(db, control=control)
    skilled = 0
    declined = 0
    eligible: list[tuple[User, OperatorAgentState]] = []
    for user, state in scoped_rows:
        capabilities = resolve_capabilities(user, db)
        if not policy.agent_is_eligible(capabilities):
            continue
        skilled += 1
        if active_decline_exists(
            db,
            request_row=request_row,
            user_id=user.id,
        ):
            declined += 1
            continue
        if state.status != "online" or not heartbeat_is_fresh(state):
            continue
        if voice:
            if not state.voice_enabled:
                continue
            occupied = active_voice_load(db, user_id=user.id)
            reserved = reserved_voice_offer_count(db, user_id=user.id)
            if occupied + reserved >= state.max_concurrent_voice_calls:
                continue
        elif (
            active_agent_load(db, user_id=user.id)
            >= state.max_concurrent_conversations
        ):
            continue
        eligible.append((user, state))
    eligible.sort(
        key=lambda item: (
            active_voice_load(db, user_id=item[0].id)
            if voice
            else active_agent_load(db, user_id=item[0].id),
            item[1].updated_at,
            item[0].id,
        )
    )
    return eligible, {
        "scoped_agents": len(scoped_rows),
        "skilled_agents": skilled,
        "available_agents": len(eligible),
        "declined_agents": declined,
    }


def create_next_voice_offer(
    db: Session,
    *,
    voice_session: WebchatVoiceSession,
) -> VoiceRoutingOffer | None:
    """Reserve one ringing agent; HandoffRequest remains ownership authority."""

    if voice_session.status not in VOICE_OPEN_SESSION_STATUSES:
        return None
    request_row = (
        db.get(WebchatHandoffRequest, voice_session.handoff_request_id)
        if voice_session.handoff_request_id is not None
        else None
    )
    if request_row is None or request_row.status != "requested":
        return None
    conversation = db.get(WebchatConversation, voice_session.conversation_id)
    if conversation is None or conversation.status != "open":
        return None
    _maybe_start_due_retry(
        db,
        request_row=request_row,
        conversation=conversation,
    )
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
        mark_routing_outcome(
            request_row,
            outcome="offered",
            reason_code="voice_offer_active",
        )
        return active_offer
    control = _control_for_conversation(db, conversation)
    candidates, stats = _candidate_rows(
        db,
        request_row=request_row,
        conversation=conversation,
        control=control,
        voice=True,
    )
    if not candidates:
        _mark_candidate_exhaustion(
            db,
            request_row=request_row,
            conversation=conversation,
            **stats,
        )
        db.flush()
        return None
    user, _state = candidates[0]
    sequence = int(
        db.query(func.coalesce(func.max(VoiceRoutingOffer.sequence), 0))
        .filter(VoiceRoutingOffer.voice_session_id == voice_session.id)
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
            seconds=_voice_offer_timeout_seconds(db, session=voice_session)
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
                VoiceRoutingOffer.voice_session_id == voice_session.id,
                VoiceRoutingOffer.status == "offered",
                VoiceRoutingOffer.expires_at > utc_now(),
            )
            .first()
        )
    mark_routing_outcome(
        request_row,
        outcome="offered",
        reason_code="voice_offer_created",
    )
    voice_session.status = "ringing"
    voice_session.ringing_at = voice_session.ringing_at or now
    voice_session.updated_at = now
    _routing_event(
        db,
        conversation=conversation,
        request_row=request_row,
        event_type="voice.offer.created",
        payload={
            "voice_session_id": voice_session.public_id,
            "offer_id": offer.public_id,
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
    voice_exists = (
        db.query(WebchatVoiceSession.id)
        .filter(
            WebchatVoiceSession.conversation_id
            == WebchatHandoffRequest.conversation_id,
            WebchatVoiceSession.status.in_(sorted(VOICE_OPEN_SESSION_STATUSES)),
        )
        .exists()
    )
    rows = (
        db.query(
            WebchatHandoffRequest,
            WebchatConversation,
            ConversationControl,
        )
        .join(
            WebchatConversation,
            WebchatConversation.id == WebchatHandoffRequest.conversation_id,
        )
        .join(
            ConversationControl,
            ConversationControl.conversation_id == WebchatConversation.id,
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
            ~voice_exists,
        )
        .order_by(
            WebchatHandoffRequest.requested_at.asc(),
            WebchatHandoffRequest.id.asc(),
        )
        .limit(200)
        .all()
    )
    ranked: list[
        tuple[
            int,
            datetime,
            int,
            WebchatHandoffRequest,
            WebchatConversation,
            ConversationControl,
        ]
    ] = []
    for request_row, conversation, control in rows:
        _maybe_start_due_retry(
            db,
            request_row=request_row,
            conversation=conversation,
        )
        retry_at = ensure_utc(request_row.routing_retry_at)
        if retry_at is not None and retry_at > (ensure_utc(utc_now()) or utc_now()):
            continue
        try:
            policy = _ensure_request_policy(
                db,
                request_row=request_row,
                conversation=conversation,
            )
        except HandoffRoutingPolicyError:
            _mark_candidate_exhaustion(
                db,
                request_row=request_row,
                conversation=conversation,
                scoped_agents=1,
                skilled_agents=0,
                available_agents=0,
                declined_agents=0,
            )
            continue
        if not policy.agent_is_eligible(resolve_capabilities(user, db)):
            continue
        if active_decline_exists(
            db,
            request_row=request_row,
            user_id=user.id,
        ):
            continue
        ranked.append(
            (
                policy.priority,
                ensure_utc(request_row.requested_at)
                or request_row.requested_at,
                request_row.id,
                request_row,
                conversation,
                control,
            )
        )
    if not ranked:
        return None
    ranked.sort(key=lambda item: (item[0], item[1], item[2]))
    _, _, _, request_row, conversation, control = ranked[0]
    return request_row, conversation, control


def assign_handoff_to_agent(
    db: Session,
    *,
    request_row: WebchatHandoffRequest,
    conversation: WebchatConversation,
    user: User,
    mode: str = "automatic",
    voice_offer: VoiceRoutingOffer | None = None,
) -> dict[str, Any]:
    state = get_or_create_agent_state(db, user_id=user.id, lock=True)
    if state.status != "online" or not heartbeat_is_fresh(state):
        raise HTTPException(status_code=409, detail="agent_not_available")
    control = _control_for_conversation(db, conversation)
    if not _scope_grant_exists(db, user=user, control=control):
        raise HTTPException(status_code=403, detail="agent_scope_not_authorized")

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
        raise HTTPException(status_code=409, detail="handoff_not_waiting")
    try:
        _ensure_request_policy(
            db,
            request_row=locked_request,
            conversation=locked_conversation,
        )
        require_user_routing_eligible(
            db,
            user=user,
            request_row=locked_request,
        )
    except HandoffRoutingPolicyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if active_decline_exists(
        db,
        request_row=locked_request,
        user_id=user.id,
    ):
        raise HTTPException(status_code=409, detail="agent_declined_current_generation")

    voice_session = _voice_session_for_conversation(
        db,
        conversation_id=locked_conversation.id,
    )
    now = utc_now()
    locked_offer: VoiceRoutingOffer | None = None
    if voice_session is not None:
        if not state.voice_enabled:
            raise HTTPException(status_code=409, detail="agent_voice_disabled")
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
            raise HTTPException(status_code=409, detail="voice_offer_not_owned")
        if locked_offer.expires_at <= now:
            raise HTTPException(status_code=409, detail="voice_offer_expired")
        other_reservations = max(
            0,
            reserved_voice_offer_count(db, user_id=user.id, now=now) - 1,
        )
        if (
            active_voice_load(db, user_id=user.id, now=now)
            + other_reservations
            >= state.max_concurrent_voice_calls
        ):
            raise HTTPException(status_code=409, detail="agent_voice_capacity_full")
    elif (
        active_agent_load(db, user_id=user.id)
        >= state.max_concurrent_conversations
    ):
        raise HTTPException(status_code=409, detail="agent_capacity_full")

    locked_request.status = "accepted"
    locked_request.accepted_by_user_id = user.id
    locked_request.assigned_agent_id = user.id
    locked_request.accepted_at = locked_request.accepted_at or now
    mark_routing_outcome(
        locked_request,
        outcome="accepted",
        reason_code="agent_accepted",
    )
    locked_request.lock_version += 1
    locked_request.updated_at = now
    locked_conversation.current_handoff_request_id = locked_request.id
    locked_conversation.handoff_status = "accepted"
    locked_conversation.active_agent_id = user.id
    locked_conversation.ai_suspended = True
    locked_conversation.ai_suspended_at = locked_conversation.ai_suspended_at or now
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
                WebchatVoiceParticipant.voice_session_id == voice_session.id,
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
    policy = request_policy(locked_request)
    task = _ensure_operator_task(
        db,
        request_row=locked_request,
        conversation=locked_conversation,
        policy=policy,
    )
    task.status = "assigned"
    task.assignee_id = user.id
    task.updated_at = now
    if locked_conversation.ticket_id is not None:
        ticket = db.get(Ticket, locked_conversation.ticket_id)
        if ticket is not None:
            ticket.assignee_id = user.id
            ticket.updated_at = now
    channel_kind = "voice" if voice_session is not None else "text"
    _routing_event(
        db,
        conversation=locked_conversation,
        request_row=locked_request,
        event_type="handoff.accepted",
        payload={
            "actor_id": user.id,
            "assignment_mode": mode,
            "channel_kind": channel_kind,
            "voice_offer_id": (
                locked_offer.public_id if locked_offer is not None else None
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
        raise HTTPException(status_code=409, detail="voice_handoff_missing")
    request_row = db.get(
        WebchatHandoffRequest,
        voice_session.handoff_request_id,
    )
    conversation = db.get(
        WebchatConversation,
        voice_session.conversation_id,
    )
    if request_row is None or conversation is None:
        raise HTTPException(status_code=409, detail="voice_handoff_missing")
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
        raise HTTPException(status_code=409, detail="voice_offer_not_owned")
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
        raise HTTPException(status_code=409, detail="voice_handoff_missing")
    request_row = db.get(
        WebchatHandoffRequest,
        voice_session.handoff_request_id,
    )
    conversation = db.get(
        WebchatConversation,
        voice_session.conversation_id,
    )
    if request_row is None or conversation is None:
        raise HTTPException(status_code=409, detail="voice_handoff_missing")
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
                VoiceRoutingOffer.voice_session_id == voice_session.id,
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
                "routing": routing_projection(request_row),
            }
        raise HTTPException(status_code=409, detail="voice_offer_not_owned")
    offer.status = "declined"
    offer.declined_at = now
    offer.decline_reason = (note or reason_code)[:240]
    offer.updated_at = now
    record_routing_decline(
        db,
        request_row=request_row,
        user_id=user.id,
        reason_code=reason_code,
        note=note,
    )
    mark_routing_outcome(
        request_row,
        outcome="waiting",
        reason_code="voice_offer_declined",
    )
    voice_session.status = "ringing"
    voice_session.updated_at = now
    conversation.handoff_status = "requested"
    conversation.active_agent_id = None
    conversation.ai_suspended = True
    conversation.ai_suspended_reason = "voice_handoff_waiting"
    conversation.takeover_mode = None
    conversation.updated_at = now
    task = _operator_task(db, request_id=request_row.id)
    if task is not None:
        task.status = "pending"
        task.assignee_id = None
        task.updated_at = now
    _routing_event(
        db,
        conversation=conversation,
        request_row=request_row,
        event_type="voice.offer.declined",
        payload={
            "voice_session_id": voice_session.public_id,
            "offer_id": offer.public_id,
            "actor_id": user.id,
            "reason_code": reason_code,
        },
    )
    db.flush()
    next_offer = create_next_voice_offer(db, voice_session=voice_session)
    return {
        "voice_session_id": voice_session.public_id,
        "offer_id": offer.public_id,
        "status": "declined",
        "next_offer_id": (
            next_offer.public_id if next_offer is not None else None
        ),
        "routing": routing_projection(request_row),
    }


def fill_agent_capacity(
    db: Session,
    *,
    user: User,
) -> list[dict[str, Any]]:
    assigned: list[dict[str, Any]] = []
    state = get_or_create_agent_state(db, user_id=user.id, lock=True)
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
            if exc.status_code == 409 and exc.detail == "handoff_not_waiting":
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
            create_next_voice_offer(db, voice_session=voice_session)
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
            WebchatHandoffRequest.conversation_id == conversation.id,
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
                existing.recommended_agent_action or recommended_agent_action
            )
            existing.trigger_message_id = (
                existing.trigger_message_id or trigger_message_id
            )
            existing.ai_turn_id = existing.ai_turn_id or ai_turn_id
            existing.updated_at = now
            policy = _ensure_request_policy(
                db,
                request_row=existing,
                conversation=conversation,
            )
            _ensure_operator_task(
                db,
                request_row=existing,
                conversation=conversation,
                policy=policy,
            )
            voice_session = _voice_session_for_conversation(
                db,
                conversation_id=conversation.id,
            )
            if voice_session is not None:
                voice_session.handoff_request_id = existing.id
                create_next_voice_offer(db, voice_session=voice_session)
            else:
                _auto_assign_text_request(
                    db,
                    request_row=existing,
                    conversation=conversation,
                    control=_control_for_conversation(db, conversation),
                )
        return existing

    policy = build_handoff_routing_policy(
        db,
        conversation=conversation,
        ticket=(
            db.get(Ticket, conversation.ticket_id)
            if conversation.ticket_id is not None
            else None
        ),
    )
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
        requested_by_actor_type=(requested_by_actor_type or "system")[:40],
        requested_by_user_id=requested_by_user_id,
        routing_generation=1,
        routing_outcome="waiting",
        routing_reason_code="handoff_requested",
        routing_owner=policy.owner_queue_key[:120],
        requested_at=now,
        created_at=now,
        updated_at=now,
    )
    persist_handoff_routing_policy(row, policy)
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
    task = _ensure_operator_task(
        db,
        request_row=row,
        conversation=conversation,
        policy=policy,
    )
    task.status = "pending"
    task.assignee_id = None
    _routing_event(
        db,
        conversation=conversation,
        request_row=row,
        event_type="handoff.requested",
        payload={
            "source": row.source,
            "trigger_type": row.trigger_type,
            "reason_code": row.reason_code,
            "channel_kind": "voice" if voice_session is not None else "text",
        },
    )
    db.flush()
    if voice_session is not None:
        create_next_voice_offer(db, voice_session=voice_session)
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
    candidates, stats = _candidate_rows(
        db,
        request_row=request_row,
        conversation=conversation,
        control=control,
        voice=False,
    )
    if not candidates:
        _mark_candidate_exhaustion(
            db,
            request_row=request_row,
            conversation=conversation,
            **stats,
        )
        db.flush()
        return
    user, _state = candidates[0]
    assign_handoff_to_agent(
        db,
        request_row=request_row,
        conversation=conversation,
        user=user,
        mode="automatic",
    )


def queue_position(
    db: Session,
    *,
    request_row: WebchatHandoffRequest,
) -> int | None:
    from .agent_availability_service import queue_position as scoped_queue_position

    return scoped_queue_position(db, request_row=request_row)


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
                    - (ensure_utc(request_row.requested_at) or request_row.requested_at)
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
                VoiceRoutingOffer.voice_session_id == voice_session.id,
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
        "recommended_agent_action": request_row.recommended_agent_action,
        "assigned_agent_id": request_row.assigned_agent_id,
        "waiting_seconds": waiting_seconds,
        "queue_position": queue_position(db, request_row=request_row),
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
            voice_session.public_id if voice_session is not None else None
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
        "routing": routing_projection(request_row),
    }


def close_conversation(
    db: Session,
    *,
    conversation: WebchatConversation,
    user: User,
    outcome: str,
    note: str | None = None,
) -> dict[str, Any]:
    """Close a ticketless or ticket-linked Conversation through one exit command."""

    normalized = str(outcome or "").strip().lower()
    if normalized not in CONVERSATION_OUTCOMES:
        raise HTTPException(status_code=400, detail="invalid_conversation_outcome")
    control = _control_for_conversation(db, conversation)
    if not _scope_grant_exists(db, user=user, control=control):
        raise HTTPException(status_code=403, detail="agent_scope_not_authorized")
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
    if conversation.ticket_id is None and normalized == "ticket_created":
        raise HTTPException(
            status_code=409,
            detail="ticketless_conversation_has_no_created_ticket",
        )
    if conversation.ticket_id is not None and normalized == "ticket_created":
        normalized = "human_resolved"

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
    if request_row is not None and request_row.status in {"requested", "accepted"}:
        request_row.status = "closed"
        request_row.closed_at = now
        request_row.decision_note = (note or "")[:1000] or None
        mark_routing_outcome(
            request_row,
            outcome="fallback_selected",
            reason_code="conversation_closed",
            fallback_action=normalized,
        )
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
    if request_row is not None:
        task = _operator_task(db, request_id=request_row.id)
        if task is not None:
            task.status = "resolved"
            task.resolved_at = now
            task.updated_at = now
    receipt = {
        "schema": "nexus.conversation-responsibility-exit.v1",
        "conversation_id": conversation.id,
        "handoff_request_id": request_row.id if request_row is not None else None,
        "previous_agent_id": previous_agent_id,
        "outcome": normalized,
        "ticket_id": conversation.ticket_id,
        "closed_by_user_id": user.id,
        "closed_at": now.isoformat(),
        "contains_payloads": False,
    }
    _event(
        db,
        conversation=conversation,
        event_type="conversation.closed",
        payload=receipt,
    )
    log_admin_audit(
        db,
        actor_id=user.id,
        action="conversation.closed",
        target_type="webchat_conversation",
        target_id=conversation.id,
        new_value=receipt,
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
        "responsibility_exit": receipt,
    }


__all__ = [
    "CONVERSATION_OUTCOMES",
    "DEFAULT_AGENT_CAPACITY",
    "DEFAULT_VOICE_CAPACITY",
    "DEFAULT_VOICE_WRAP_UP_SECONDS",
    "HEARTBEAT_TTL_SECONDS",
    "MAX_AGENT_CAPACITY",
    "MAX_VOICE_CAPACITY",
    "MAX_VOICE_WRAP_UP_SECONDS",
    "VOICE_OPEN_SESSION_STATUSES",
    "accept_voice_offer",
    "active_agent_load",
    "active_voice_load",
    "assign_handoff_to_agent",
    "availability_summary",
    "close_conversation",
    "create_next_voice_offer",
    "decline_voice_offer",
    "expire_voice_offers",
    "fill_agent_capacity",
    "get_or_create_agent_state",
    "heartbeat_agent",
    "heartbeat_is_fresh",
    "queue_position",
    "read_agent_state",
    "release_expired_voice_wrap_ups",
    "request_handoff",
    "reserved_voice_offer_count",
    "serialize_handoff",
    "set_agent_state",
]
