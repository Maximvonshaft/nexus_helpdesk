from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from ..enums import ConversationState, TicketStatus
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
from . import agent_routing_service as routing
from .audit_service import log_admin_audit
from .handoff_routing_authority import (
    activate_due_generation,
    record_candidate_attempt,
)

# An untouched accepted text handoff is not allowed to occupy capacity forever.
# The short threshold is used only when the assigned operator is demonstrably
# offline/stale. A currently online operator gets a much wider no-response window.
OFFLINE_HANDOFF_GRACE_SECONDS = 5 * 60
UNTOUCHED_HANDOFF_TIMEOUT_SECONDS = 30 * 60

_ATTEMPT_OUTCOME_BY_REASON = {
    "assigned_agent_heartbeat_stale": "unavailable",
    "accepted_handoff_untouched_timeout": "expired",
}


def _candidate_attempt_outcome(reason_code: str) -> str:
    try:
        return _ATTEMPT_OUTCOME_BY_REASON[reason_code]
    except KeyError as exc:
        raise ValueError("stale_handoff_reason_code_invalid") from exc


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
            WebchatVoiceSession.status.in_(
                sorted(routing._core.VOICE_OPEN_SESSION_STATUSES)
            ),
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
        or heartbeat
        < current - timedelta(seconds=routing._core.HEARTBEAT_TTL_SECONDS)
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
        outcome=_candidate_attempt_outcome(reason_code),
        reason_code=reason_code,
    )
    if plan is None or plan.status != "assigned":
        return

    # Requeue is a new routing generation, not a return to the old candidate set.
    # Advancing immediately prevents a one-agent queue from becoming permanently
    # unroutable while still retaining the abandoned assignment as evidence.
    now = utc_now()
    plan.status = "retry_scheduled"
    plan.assigned_agent_id = None
    plan.outcome_code = reason_code
    plan.next_retry_at = now
    plan.updated_at = now
    activate_due_generation(db, plan=plan)
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
    previous_accepted_at = request_row.accepted_at
    request_row.status = "requested"
    request_row.assigned_agent_id = None
    request_row.accepted_by_user_id = None
    # A requeue creates a new acceptance generation. Clearing the old clock makes
    # both canonical acceptance paths establish a fresh response window.
    request_row.accepted_at = None
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
        if ticket is not None:
            if ticket.assignee_id == previous_agent_id:
                ticket.assignee_id = None
            ticket.status = TicketStatus.pending_assignment
            ticket.conversation_state = ConversationState.human_review_required
            ticket.required_action = (
                request_row.recommended_agent_action
                or request_row.reason_code
                or "WebChat handoff waiting for human support"
            )
            ticket.updated_at = now

    _reopen_routing_plan(
        db,
        request_row=request_row,
        previous_agent_id=previous_agent_id,
        reason_code=reason_code,
    )
    routing._core._event(
        db,
        conversation=conversation,
        event_type="handoff.requeued",
        payload={
            "handoff_request_id": request_row.id,
            "previous_agent_id": previous_agent_id,
            "previous_accepted_at": (
                previous_accepted_at.isoformat() if previous_accepted_at else None
            ),
            "reason_code": reason_code,
        },
    )
    log_admin_audit(
        db,
        actor_id=None,
        action="webchat_handoff.requeued",
        target_type="webchat_handoff_request",
        target_id=request_row.id,
        old_value={
            "assigned_agent_id": previous_agent_id,
            "status": "accepted",
            "accepted_at": (
                previous_accepted_at.isoformat() if previous_accepted_at else None
            ),
        },
        new_value={
            "assigned_agent_id": None,
            "status": "requested",
            "accepted_at": None,
            "reason_code": reason_code,
        },
    )
    db.flush()


def _lock_candidate_rows(db: Session, query):
    if not db.bind or not db.bind.dialect.name.startswith("postgresql"):
        return query
    # An unqualified PostgreSQL FOR UPDATE expands to every joined relation and
    # is invalid for the nullable side of the OperatorAgentState outer join.
    # Lock only the authoritative rows this reconciliation can mutate.
    return query.with_for_update(
        of=(WebchatHandoffRequest, WebchatConversation),
        skip_locked=True,
    )


def reconcile_stale_text_handoffs(
    db: Session,
    *,
    assigned_agent_id: int | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Requeue abandoned accepted text Handoffs without closing customer work."""

    now = utc_now()
    acceptance_clock = func.coalesce(
        WebchatHandoffRequest.accepted_at,
        WebchatHandoffRequest.updated_at,
    )
    offline_cutoff = now - timedelta(seconds=OFFLINE_HANDOFF_GRACE_SECONDS)
    untouched_cutoff = now - timedelta(seconds=UNTOUCHED_HANDOFF_TIMEOUT_SECONDS)
    agent_stale = or_(
        OperatorAgentState.user_id.is_(None),
        OperatorAgentState.status != "online",
        OperatorAgentState.last_heartbeat_at.is_(None),
        OperatorAgentState.last_heartbeat_at
        < now - timedelta(seconds=routing._core.HEARTBEAT_TTL_SECONDS),
    )
    post_acceptance_agent_reply = (
        db.query(WebchatMessage.id)
        .filter(
            WebchatMessage.conversation_id
            == WebchatHandoffRequest.conversation_id,
            WebchatMessage.direction == "agent",
            WebchatMessage.author_user_id
            == WebchatHandoffRequest.assigned_agent_id,
            WebchatMessage.created_at >= acceptance_clock,
        )
        .exists()
    )
    open_voice_session = (
        db.query(WebchatVoiceSession.id)
        .filter(
            WebchatVoiceSession.conversation_id
            == WebchatHandoffRequest.conversation_id,
            WebchatVoiceSession.status.in_(
                sorted(routing._core.VOICE_OPEN_SESSION_STATUSES)
            ),
        )
        .exists()
    )
    query = (
        db.query(WebchatHandoffRequest, WebchatConversation)
        .join(
            WebchatConversation,
            WebchatConversation.id == WebchatHandoffRequest.conversation_id,
        )
        .outerjoin(
            OperatorAgentState,
            OperatorAgentState.user_id
            == WebchatHandoffRequest.assigned_agent_id,
        )
        .filter(
            WebchatHandoffRequest.status == "accepted",
            WebchatHandoffRequest.assigned_agent_id.isnot(None),
            WebchatConversation.status == "open",
            ~open_voice_session,
            or_(
                and_(acceptance_clock <= offline_cutoff, agent_stale),
                and_(
                    acceptance_clock <= untouched_cutoff,
                    ~post_acceptance_agent_reply,
                ),
            ),
        )
    )
    if assigned_agent_id is not None:
        query = query.filter(
            WebchatHandoffRequest.assigned_agent_id == int(assigned_agent_id)
        )
    query = query.order_by(
        acceptance_clock.asc(),
        WebchatHandoffRequest.id.asc(),
    )
    query = _lock_candidate_rows(db, query)
    rows = query.limit(max(1, min(int(limit or 100), 500))).all()

    inspected = 0
    released = 0
    released_ids: list[int] = []
    for request_row, conversation in rows:
        inspected += 1
        # Defend against state changes between candidate selection and mutation.
        if _has_open_voice_session(db, conversation_id=conversation.id):
            continue
        accepted_at = ensure_utc(request_row.accepted_at or request_row.updated_at)
        current = ensure_utc(now)
        if accepted_at is None or current is None:
            continue
        age_seconds = (current - accepted_at).total_seconds()
        agent_is_stale = _agent_is_stale(
            db,
            agent_id=int(request_row.assigned_agent_id),
            now=now,
        )
        replied = _is_post_acceptance_agent_message_present(
            db,
            request_row=request_row,
        )
        reason_code = None
        if agent_is_stale and age_seconds >= OFFLINE_HANDOFF_GRACE_SECONDS:
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
