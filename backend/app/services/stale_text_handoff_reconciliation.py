from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from ..models import Ticket
from ..models_agent_routing import OperatorAgentState
from ..models_handoff_routing import HandoffRoutingPlan
from ..operator_models import OperatorTask
from ..utils.time import ensure_utc, utc_now
from ..voice_models import WebchatVoiceSession
from ..webchat_models import (
    WebchatConversation,
    WebchatHandoffRequest,
    WebchatMessage,
)
from .agent_routing_primitives import (
    HEARTBEAT_TTL_SECONDS,
    VOICE_OPEN_SESSION_STATUSES,
    _event,
)
from .audit_service import log_admin_audit
from .handoff_routing_authority import record_candidate_attempt

# An untouched accepted text handoff is not allowed to occupy capacity forever.
# The short threshold is used only when the assigned operator is demonstrably
# offline/stale. A currently online operator gets a much wider no-response window.
OFFLINE_HANDOFF_GRACE_SECONDS = 5 * 60
UNTOUCHED_HANDOFF_TIMEOUT_SECONDS = 30 * 60


def _is_post_acceptance_agent_message_present(
    db: Session,
    *,
    request_row: WebchatHandoffRequest,
) -> bool:
    if request_row.assigned_agent_id is None:
        return False
    query = db.query(WebchatMessage.id).filter(
        WebchatMessage.conversation_id == request_row.conversation_id,
        WebchatMessage.direction == "agent",
        WebchatMessage.author_user_id == request_row.assigned_agent_id,
    )
    if request_row.accepted_at is not None:
        query = query.filter(WebchatMessage.created_at >= request_row.accepted_at)
    return query.first() is not None


def _has_open_voice_session(db: Session, *, conversation_id: int) -> bool:
    return bool(
        db.query(WebchatVoiceSession.id)
        .filter(
            WebchatVoiceSession.conversation_id == conversation_id,
            WebchatVoiceSession.status.in_(sorted(VOICE_OPEN_SESSION_STATUSES)),
        )
        .first()
    )


def _agent_is_stale(
    db: Session,
    *,
    agent_id: int,
    now,
) -> bool:
    state = (
        db.query(OperatorAgentState)
        .filter(OperatorAgentState.user_id == agent_id)
        .one_or_none()
    )
    if state is None or state.status != "online":
        return True
    heartbeat = ensure_utc(state.last_heartbeat_at)
    current = ensure_utc(now)
    return bool(
        heartbeat is None
        or current is None
        or heartbeat < current - timedelta(seconds=HEARTBEAT_TTL_SECONDS)
    )


def _reopen_routing_plan(
    db: Session,
    *,
    request_row: WebchatHandoffRequest,
    previous_agent_id: int,
    reason_code: str,
) -> None:
    plan_query = db.query(HandoffRoutingPlan).filter(
        HandoffRoutingPlan.request_id == request_row.id
    )
    if db.bind and db.bind.dialect.name.startswith("postgresql"):
        plan_query = plan_query.with_for_update()
    plan = plan_query.one_or_none()
    record_candidate_attempt(
        db,
        plan=plan,
        request_id=request_row.id,
        agent_id=previous_agent_id,
        channel_kind="text",
        outcome="released",
        reason_code=reason_code,
    )
    if plan is None or plan.status != "assigned":
        return
    plan.status = "active"
    plan.assigned_agent_id = None
    plan.outcome_code = reason_code
    plan.next_retry_at = None
    plan.updated_at = utc_now()
    db.flush()


def _release_one(
    db: Session,
    *,
    request_row: WebchatHandoffRequest,
    conversation: WebchatConversation,
    reason_code: str,
) -> None:
    previous_agent_id = int(request_row.assigned_agent_id or 0)
    if previous_agent_id <= 0:
        return
    now = utc_now()
    request_row.status = "requested"
    request_row.assigned_agent_id = None
    request_row.released_at = now
    request_row.expires_at = None
    request_row.decision_note = reason_code
    request_row.lock_version += 1
    request_row.updated_at = now

    conversation.current_handoff_request_id = request_row.id
    conversation.handoff_status = "requested"
    conversation.active_agent_id = None
    conversation.ai_suspended = True
    conversation.ai_suspended_reason = reason_code
    conversation.takeover_mode = None
    conversation.updated_at = now

    task = (
        db.query(OperatorTask)
        .filter(
            OperatorTask.webchat_conversation_id == conversation.id,
            OperatorTask.task_type == "handoff",
            OperatorTask.status.notin_(["resolved", "cancelled", "dropped"]),
        )
        .order_by(OperatorTask.id.desc())
        .first()
    )
    if task is not None:
        task.status = "pending"
        task.assignee_id = None
        task.updated_at = now

    if conversation.ticket_id is not None:
        ticket = db.get(Ticket, conversation.ticket_id)
        if ticket is not None and ticket.assignee_id == previous_agent_id:
            ticket.assignee_id = None
            ticket.updated_at = now

    _reopen_routing_plan(
        db,
        request_row=request_row,
        previous_agent_id=previous_agent_id,
        reason_code=reason_code,
    )
    _event(
        db,
        conversation=conversation,
        event_type="handoff.requeued",
        payload={
            "handoff_request_id": request_row.id,
            "previous_agent_id": previous_agent_id,
            "reason_code": reason_code,
        },
    )
    log_admin_audit(
        db,
        actor_id=None,
        action="webchat_handoff.requeued",
        target_type="webchat_handoff_request",
        target_id=request_row.id,
        old_value={"assigned_agent_id": previous_agent_id, "status": "accepted"},
        new_value={
            "assigned_agent_id": None,
            "status": "requested",
            "reason_code": reason_code,
        },
    )
    db.flush()


def reconcile_stale_text_handoffs(
    db: Session,
    *,
    assigned_agent_id: int | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Requeue abandoned accepted text Handoffs without closing customer work."""

    now = utc_now()
    query = (
        db.query(WebchatHandoffRequest, WebchatConversation)
        .join(
            WebchatConversation,
            WebchatConversation.id == WebchatHandoffRequest.conversation_id,
        )
        .filter(
            WebchatHandoffRequest.status == "accepted",
            WebchatHandoffRequest.assigned_agent_id.isnot(None),
            WebchatConversation.status == "open",
        )
    )
    if assigned_agent_id is not None:
        query = query.filter(
            WebchatHandoffRequest.assigned_agent_id == int(assigned_agent_id)
        )
    query = query.order_by(
        WebchatHandoffRequest.accepted_at.asc(),
        WebchatHandoffRequest.id.asc(),
    )
    if db.bind and db.bind.dialect.name.startswith("postgresql"):
        query = query.with_for_update(skip_locked=True)
    rows = query.limit(max(1, min(int(limit or 100), 500))).all()

    inspected = 0
    released = 0
    released_ids: list[int] = []
    for request_row, conversation in rows:
        inspected += 1
        if _has_open_voice_session(db, conversation_id=conversation.id):
            continue
        accepted_at = ensure_utc(request_row.accepted_at or request_row.updated_at)
        current = ensure_utc(now)
        if accepted_at is None or current is None:
            continue
        age_seconds = (current - accepted_at).total_seconds()
        agent_stale = _agent_is_stale(
            db,
            agent_id=int(request_row.assigned_agent_id),
            now=now,
        )
        replied = _is_post_acceptance_agent_message_present(
            db,
            request_row=request_row,
        )
        reason_code = None
        if agent_stale and age_seconds >= OFFLINE_HANDOFF_GRACE_SECONDS:
            reason_code = "assigned_agent_heartbeat_stale"
        elif not replied and age_seconds >= UNTOUCHED_HANDOFF_TIMEOUT_SECONDS:
            reason_code = "accepted_handoff_untouched_timeout"
        if reason_code is None:
            continue
        _release_one(
            db,
            request_row=request_row,
            conversation=conversation,
            reason_code=reason_code,
        )
        released += 1
        released_ids.append(request_row.id)

    return {
        "inspected": inspected,
        "released": released,
        "released_request_ids": released_ids,
        "offline_grace_seconds": OFFLINE_HANDOFF_GRACE_SECONDS,
        "untouched_timeout_seconds": UNTOUCHED_HANDOFF_TIMEOUT_SECONDS,
    }


__all__ = [
    "OFFLINE_HANDOFF_GRACE_SECONDS",
    "UNTOUCHED_HANDOFF_TIMEOUT_SECONDS",
    "reconcile_stale_text_handoffs",
]
