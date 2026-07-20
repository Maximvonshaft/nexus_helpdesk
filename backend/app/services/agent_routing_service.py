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
    """Count accepted, still-open handoffs; this is the sole occupancy authority."""

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
        )
        .scalar()
        or 0
    )


def _state_payload(db: Session, row: OperatorAgentState) -> dict[str, Any]:
    load = active_agent_load(db, user_id=row.user_id)
    fresh = heartbeat_is_fresh(row)
    assignable = row.status == "online" and fresh
    available = (
        max(0, row.max_concurrent_conversations - load) if assignable else 0
    )
    return {
        "user_id": row.user_id,
        "status": row.status,
        "heartbeat_fresh": fresh,
        "assignable": assignable,
        "max_concurrent_conversations": row.max_concurrent_conversations,
        "active_conversations": load,
        "available_capacity": available,
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
    if row.status != normalized:
        row.status = normalized
        row.status_changed_at = now
    row.last_heartbeat_at = now if normalized in {"online", "paused"} else None
    row.updated_at = now
    db.flush()
    if normalized == "online":
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
    """Lock the oldest request the agent can actually serve.

    Scope eligibility and this agent's prior declines are part of the SQL
    candidate set, so unrelated global backlog cannot starve later eligible work.
    """

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
    )
    return _lock(query, db).first()


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
    if active_agent_load(db, user_id=user.id) >= state.max_concurrent_conversations:
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
        if active_agent_load(db, user_id=user.id) >= state.max_concurrent_conversations:
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
