from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from ..models import Ticket, User
from ..models_agent_routing import ConversationControl, OperatorAgentState
from ..operator_models import OperatorQueueScopeGrant, OperatorTask
from ..utils.time import ensure_utc, utc_now
from ..voice_models import WebchatVoiceSession
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
VOICE_ACTIVE_STATUSES = {"created", "ringing", "accepted", "active"}


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
        payload_json=json.dumps(payload or {}, ensure_ascii=False, default=str),
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
            max_concurrent_voice_calls=DEFAULT_VOICE_CAPACITY,
            voice_wrap_up_seconds=DEFAULT_VOICE_WRAP_UP_SECONDS,
            status_changed_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.flush()
    return row


def heartbeat_is_fresh(row: OperatorAgentState, *, now=None) -> bool:
    heartbeat = ensure_utc(row.last_heartbeat_at)
    current = ensure_utc(now or utc_now())
    if heartbeat is None or current is None:
        return False
    return heartbeat >= current - timedelta(seconds=HEARTBEAT_TTL_SECONDS)


def active_agent_load(db: Session, *, user_id: int) -> int:
    """Count accepted text handoffs; voice ownership has its own capacity authority."""

    now = utc_now()
    voice_occupancy = (
        db.query(WebchatVoiceSession.id)
        .filter(
            WebchatVoiceSession.conversation_id
            == WebchatHandoffRequest.conversation_id,
            WebchatVoiceSession.accepted_by_user_id == user_id,
            (
                WebchatVoiceSession.status.in_(list(VOICE_ACTIVE_STATUSES))
                | (
                    WebchatVoiceSession.wrap_up_expires_at.isnot(None)
                    & (WebchatVoiceSession.wrap_up_expires_at > now)
                )
            ),
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


def active_voice_load(db: Session, *, user_id: int, now=None) -> int:
    """Count active calls and bounded after-call work for one operator."""

    current = ensure_utc(now or utc_now()) or utc_now()
    return int(
        db.query(func.count(WebchatVoiceSession.id))
        .filter(
            WebchatVoiceSession.accepted_by_user_id == user_id,
            (
                WebchatVoiceSession.status.in_(list(VOICE_ACTIVE_STATUSES))
                | (
                    WebchatVoiceSession.wrap_up_expires_at.isnot(None)
                    & (WebchatVoiceSession.wrap_up_expires_at > current)
                )
            ),
        )
        .scalar()
        or 0
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
            WebchatVoiceSession.status.in_(["created", "ringing", "accepted", "active"]),
        )
        .order_by(WebchatVoiceSession.id.desc())
        .first()
    )


def _request_requires_voice_capacity(
    db: Session,
    *,
    request_row: WebchatHandoffRequest,
) -> bool:
    return _voice_session_for_conversation(
        db,
        conversation_id=request_row.conversation_id,
    ) is not None


def release_expired_voice_wrap_ups(
    db: Session,
    *,
    user_id: int | None = None,
    limit: int = 100,
) -> int:
    """Release stale voice ownership without reviving AI or losing follow-up evidence."""

    now = utc_now()
    query = db.query(WebchatVoiceSession).filter(
        WebchatVoiceSession.wrap_up_expires_at.isnot(None),
        WebchatVoiceSession.wrap_up_expires_at <= now,
    )
    if user_id is not None:
        query = query.filter(WebchatVoiceSession.accepted_by_user_id == user_id)
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
        if request_row is not None and request_row.status == "accepted":
            request_row.status = "closed"
            request_row.closed_at = now
            request_row.decision_note = "voice_wrap_up_expired"
            request_row.lock_version += 1
            request_row.updated_at = now
        if conversation is not None and conversation.active_agent_id == session.accepted_by_user_id:
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
                    "previous_agent_id": session.accepted_by_user_id,
                },
            )
        released += 1
    if released:
        db.flush()
    return released


def _state_payload(db: Session, row: OperatorAgentState) -> dict[str, Any]:
    load = active_agent_load(db, user_id=row.user_id)
    voice_load = active_voice_load(db, user_id=row.user_id)
    fresh = heartbeat_is_fresh(row)
    assignable = row.status == "online" and fresh
    available = (
        max(0, row.max_concurrent_conversations - load) if assignable else 0
    )
    available_voice = (
        max(0, row.max_concurrent_voice_calls - voice_load) if assignable else 0
    )
    return {
        "user_id": row.user_id,
        "status": row.status,
        "heartbeat_fresh": fresh,
        "assignable": assignable,
        "max_concurrent_conversations": row.max_concurrent_conversations,
        "active_conversations": load,
        "available_capacity": available,
        "max_concurrent_voice_calls": row.max_concurrent_voice_calls,
        "active_voice_calls": voice_load,
        "available_voice_capacity": available_voice,
        "voice_wrap_up_seconds": row.voice_wrap_up_seconds,
        "last_heartbeat_at": (
            row.last_heartbeat_at.isoformat() if row.last_heartbeat_at else None
        ),
        "heartbeat_ttl_seconds": HEARTBEAT_TTL_SECONDS,
    }


def read_agent_state(db: Session, *, user_id: int) -> dict[str, Any]:
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
    max_concurrent_voice_calls: int | None = None,
    voice_wrap_up_seconds: int | None = None,
) -> dict[str, Any]:
    normalized = str(presence_status or "").strip().lower()
    if normalized not in PRESENCE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_agent_presence_status",
        )
    row = get_or_create_agent_state(db, user_id=user.id, lock=True)
    old = _state_payload(db, row)
    now = utc_now()
    if max_concurrent_conversations is not None:
        capacity = int(max_concurrent_conversations)
        if not 1 <= capacity <= MAX_AGENT_CAPACITY:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid_agent_capacity",
            )
        row.max_concurrent_conversations = capacity
    if max_concurrent_voice_calls is not None:
        voice_capacity = int(max_concurrent_voice_calls)
        if not 1 <= voice_capacity <= MAX_VOICE_CAPACITY:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid_agent_voice_capacity",
            )
        row.max_concurrent_voice_calls = voice_capacity
    if voice_wrap_up_seconds is not None:
        wrap_up = int(voice_wrap_up_seconds)
        if not 0 <= wrap_up <= MAX_VOICE_WRAP_UP_SECONDS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid_agent_voice_wrap_up",
            )
        row.voice_wrap_up_seconds = wrap_up
    if row.status != normalized:
        row.status = normalized
        row.status_changed_at = now
    row.last_heartbeat_at = now if normalized in {"online", "paused"} else None
    row.updated_at = now
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
    """Normal assignment always requires an explicit active scope grant."""

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


def _eligible_requested_handoff(
    db: Session,
    *,
    user: User,
) -> tuple[WebchatHandoffRequest, WebchatConversation, ConversationControl] | None:
    """Lock the oldest scope-eligible request this agent has channel capacity for."""

    declined_exists = (
        db.query(WebchatHandoffDecision.id)
        .filter(
            WebchatHandoffDecision.request_id == WebchatHandoffRequest.id,
            WebchatHandoffDecision.actor_id == user.id,
            WebchatHandoffDecision.decision == "declined",
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
                OperatorQueueScopeGrant.tenant_key == ConversationControl.tenant_key,
                OperatorQueueScopeGrant.country_code == ConversationControl.country_code,
                OperatorQueueScopeGrant.channel_key == ConversationControl.channel_key,
                OperatorQueueScopeGrant.enabled.is_(True),
            ),
        )
        .filter(
            WebchatHandoffRequest.status == "requested",
            WebchatConversation.status == "open",
            ConversationControl.country_code.is_not(None),
            ~declined_exists,
        )
        .order_by(
            WebchatHandoffRequest.requested_at.asc(),
            WebchatHandoffRequest.id.asc(),
        )
        .limit(100)
    )
    rows = _lock(query, db).all()
    state = get_or_create_agent_state(db, user_id=user.id, lock=True)
    text_full = active_agent_load(db, user_id=user.id) >= state.max_concurrent_conversations
    voice_full = active_voice_load(db, user_id=user.id) >= state.max_concurrent_voice_calls
    for row in rows:
        request_row, _conversation, _control = row
        requires_voice = _request_requires_voice_capacity(db, request_row=request_row)
        if requires_voice and voice_full:
            continue
        if not requires_voice and text_full:
            continue
        return row
    return None


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
                ["resolved", "dropped", "replayed", "replay_failed", "cancelled"]
            ),
        )
        .order_by(OperatorTask.id.desc())
        .first()
    )


def assign_handoff_to_agent(
    db: Session,
    *,
    request_row: WebchatHandoffRequest,
    conversation: WebchatConversation,
    user: User,
    mode: str = "automatic",
) -> dict[str, Any]:
    state = get_or_create_agent_state(db, user_id=user.id, lock=True)
    if state.status != "online" or not heartbeat_is_fresh(state):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="agent_not_available",
        )
    voice_session = _voice_session_for_conversation(
        db,
        conversation_id=conversation.id,
    )
    if voice_session is not None:
        if active_voice_load(db, user_id=user.id) >= state.max_concurrent_voice_calls:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="agent_voice_capacity_full",
            )
    elif active_agent_load(db, user_id=user.id) >= state.max_concurrent_conversations:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="agent_capacity_full",
        )
    control = _control_for_conversation(db, conversation)
    if not _scope_grant_exists(db, user=user, control=control):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="agent_scope_not_authorized",
        )
    locked_request = _lock(
        db.query(WebchatHandoffRequest).filter(
            WebchatHandoffRequest.id == request_row.id
        ),
        db,
    ).first()
    if locked_request is None or locked_request.status != "requested":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="handoff_not_waiting",
        )

    now = utc_now()
    locked_request.status = "accepted"
    locked_request.accepted_by_user_id = user.id
    locked_request.assigned_agent_id = user.id
    locked_request.accepted_at = locked_request.accepted_at or now
    locked_request.lock_version += 1
    locked_request.updated_at = now
    if voice_session is not None:
        voice_session.handoff_request_id = locked_request.id
        voice_session.accepted_by_user_id = user.id
        voice_session.accepted_at = None
        voice_session.updated_at = now
    conversation.current_handoff_request_id = locked_request.id
    conversation.handoff_status = "accepted"
    conversation.active_agent_id = user.id
    conversation.ai_suspended = True
    conversation.ai_suspended_at = conversation.ai_suspended_at or now
    conversation.ai_suspended_by = user.id
    conversation.ai_suspended_reason = "handoff_accepted"
    conversation.takeover_mode = mode
    conversation.updated_at = now
    cancel_open_ai_turns_for_handoff(
        db,
        conversation=conversation,
        actor_id=user.id,
        reason_code="handoff_accepted",
    )
    task = _operator_task(db, conversation_id=conversation.id)
    if task is not None:
        task.status = "assigned"
        task.assignee_id = user.id
        task.updated_at = now
    if conversation.ticket_id is not None:
        ticket = db.get(Ticket, conversation.ticket_id)
        if ticket is not None:
            ticket.assignee_id = user.id
            ticket.updated_at = now
    _event(
        db,
        conversation=conversation,
        event_type="handoff.accepted",
        payload={
            "handoff_request_id": locked_request.id,
            "actor_id": user.id,
            "assignment_mode": mode,
            "channel_kind": "voice" if voice_session is not None else "text",
        },
    )
    log_admin_audit(
        db,
        actor_id=user.id,
        action="webchat_handoff.accepted",
        target_type="webchat_handoff_request",
        target_id=locked_request.id,
        new_value={
            "conversation_id": conversation.id,
            "assigned_agent_id": user.id,
            "assignment_mode": mode,
            "channel_kind": "voice" if voice_session is not None else "text",
        },
    )
    db.flush()
    return serialize_handoff(
        db,
        request_row=locked_request,
        conversation=conversation,
    )


def fill_agent_capacity(db: Session, *, user: User) -> list[dict[str, Any]]:
    assigned: list[dict[str, Any]] = []
    while True:
        state = get_or_create_agent_state(db, user_id=user.id, lock=True)
        if state.status != "online" or not heartbeat_is_fresh(state):
            break
        text_full = active_agent_load(db, user_id=user.id) >= state.max_concurrent_conversations
        voice_full = active_voice_load(db, user_id=user.id) >= state.max_concurrent_voice_calls
        if text_full and voice_full:
            break
        candidate = _eligible_requested_handoff(db, user=user)
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
            if exc.status_code == status.HTTP_409_CONFLICT and exc.detail == "handoff_not_waiting":
                continue
            if exc.status_code == status.HTTP_409_CONFLICT:
                break
            raise
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
        return existing

    row = WebchatHandoffRequest(
        conversation_id=conversation.id,
        ticket_id=conversation.ticket_id,
        source=(source or "ai_auto")[:40],
        trigger_type=(trigger_type or "handoff_required")[:80],
        status="requested",
        reason_code=(reason_code or "human_review_required")[:160],
        reason_text=(reason_text or "")[:240] or None,
        recommended_agent_action=(recommended_agent_action or "")[:1000] or None,
        trigger_message_id=trigger_message_id,
        ai_turn_id=ai_turn_id,
        requested_by_actor_type=(requested_by_actor_type or "system")[:40],
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
        },
    )
    task.status = "pending"
    _event(
        db,
        conversation=conversation,
        event_type="handoff.requested",
        payload={
            "handoff_request_id": row.id,
            "source": row.source,
            "trigger_type": row.trigger_type,
            "reason_code": row.reason_code,
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
        },
    )
    db.flush()
    _auto_assign_request(
        db,
        request_row=row,
        conversation=conversation,
        control=control,
    )
    return row


def _auto_assign_request(
    db: Session,
    *,
    request_row: WebchatHandoffRequest,
    conversation: WebchatConversation,
    control: ConversationControl,
) -> None:
    """Fill eligible agents from the FIFO queue; never assign the newest row directly."""

    del conversation  # The request/control are the routing facts needed here.
    candidates = (
        db.query(User, OperatorAgentState)
        .join(OperatorAgentState, OperatorAgentState.user_id == User.id)
        .filter(
            User.is_active.is_(True),
            OperatorAgentState.status == "online",
        )
        .order_by(OperatorAgentState.updated_at.asc(), User.id.asc())
        .all()
    )
    for user, state in candidates:
        if request_row.status != "requested":
            return
        if not heartbeat_is_fresh(state):
            continue
        if active_agent_load(db, user_id=user.id) >= state.max_concurrent_conversations:
            continue
        if not _scope_grant_exists(db, user=user, control=control):
            continue
        fill_agent_capacity(db, user=user)


def queue_position(
    db: Session,
    *,
    request_row: WebchatHandoffRequest,
) -> int | None:
    # Delegate to the single scope-aware availability authority after imports settle.
    from .agent_availability_service import queue_position as scoped_queue_position

    return scoped_queue_position(db, request_row=request_row)


def availability_summary(
    db: Session,
    *,
    tenant_key: str,
    country_code: str | None,
    channel_key: str,
    request_row: WebchatHandoffRequest | None = None,
) -> dict[str, Any]:
    # Public import delegates calculation to the single scope-aware service.
    from .agent_availability_service import (
        availability_summary as scoped_availability_summary,
    )

    return scoped_availability_summary(
        db,
        tenant_key=tenant_key,
        country_code=country_code,
        channel_key=channel_key,
        request_row=request_row,
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
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_conversation_outcome",
        )
    control = _control_for_conversation(db, conversation)
    if not _scope_grant_exists(db, user=user, control=control):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
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
            status_code=status.HTTP_403_FORBIDDEN,
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
    if request_row is not None and request_row.status in {"requested", "accepted"}:
        request_row.status = "closed"
        request_row.closed_at = now
        request_row.decision_note = (note or "")[:1000] or None
        request_row.lock_version += 1
        request_row.updated_at = now

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
        new_value={"outcome": normalized, "ticket_id": conversation.ticket_id},
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

def decline_voice_handoff_offer(
    db: Session,
    *,
    voice_session: WebchatVoiceSession,
    user: User,
    reason_code: str = "agent_skipped_voice_offer",
    note: str | None = None,
) -> dict[str, Any]:
    """Decline only this operator's offer and return the same call to the queue."""

    if voice_session.handoff_request_id is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="voice_handoff_missing")
    request_row = _lock(
        db.query(WebchatHandoffRequest).filter(
            WebchatHandoffRequest.id == voice_session.handoff_request_id
        ),
        db,
    ).first()
    conversation = _lock(
        db.query(WebchatConversation).filter(
            WebchatConversation.id == voice_session.conversation_id
        ),
        db,
    ).first()
    if request_row is None or conversation is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="voice_handoff_missing")
    if request_row.status != "accepted" or request_row.assigned_agent_id != user.id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="voice_offer_not_owned")

    now = utc_now()
    db.add(
        WebchatHandoffDecision(
            request_id=request_row.id,
            actor_id=user.id,
            decision="declined",
            reason_code=(reason_code or "agent_skipped_voice_offer")[:160],
            note=(note or "")[:1000] or None,
            created_at=now,
        )
    )
    request_row.status = "requested"
    request_row.assigned_agent_id = None
    request_row.accepted_by_user_id = None
    request_row.accepted_at = None
    request_row.decision_note = (note or "")[:1000] or None
    request_row.lock_version += 1
    request_row.updated_at = now
    voice_session.accepted_by_user_id = None
    voice_session.accepted_at = None
    voice_session.active_at = None
    voice_session.status = "ringing"
    voice_session.updated_at = now
    conversation.current_handoff_request_id = request_row.id
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
            "actor_id": user.id,
            "reason_code": reason_code,
        },
    )
    log_admin_audit(
        db,
        actor_id=user.id,
        action="webcall.voice.offer_declined",
        target_type="webchat_voice_session",
        target_id=voice_session.id,
        new_value={
            "handoff_request_id": request_row.id,
            "reason_code": reason_code,
        },
    )
    db.flush()
    control = _control_for_conversation(db, conversation)
    _auto_assign_request(
        db,
        request_row=request_row,
        conversation=conversation,
        control=control,
    )
    return serialize_handoff(db, request_row=request_row, conversation=conversation)
