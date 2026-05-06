from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from ..enums import ConversationState
from ..models import OpenClawUnresolvedEvent, Ticket
from ..operator_models import OperatorTask
from ..utils.time import utc_now
from ..webchat_models import WebchatConversation
from .webchat_ai_turn_service import write_webchat_event

TERMINAL_STATUSES = {"resolved", "dropped", "replayed"}


def _json_payload(payload: dict[str, Any] | None) -> str | None:
    if not payload:
        return None
    return json.dumps(payload, ensure_ascii=False, default=str)


def _loads(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except Exception:
        return {"raw": value[:500]}


def serialize_operator_task(row: OperatorTask) -> dict[str, Any]:
    return {
        "id": row.id,
        "source_type": row.source_type,
        "source_id": row.source_id,
        "ticket_id": row.ticket_id,
        "webchat_conversation_id": row.webchat_conversation_id,
        "unresolved_event_id": row.unresolved_event_id,
        "task_type": row.task_type,
        "status": row.status,
        "priority": row.priority,
        "assignee_id": row.assignee_id,
        "reason_code": row.reason_code,
        "payload_json": _loads(row.payload_json),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
    }


def create_operator_task(
    db: Session,
    *,
    source_type: str,
    task_type: str,
    reason_code: str | None = None,
    source_id: str | None = None,
    ticket_id: int | None = None,
    webchat_conversation_id: int | None = None,
    unresolved_event_id: int | None = None,
    priority: int = 100,
    payload: dict[str, Any] | None = None,
) -> OperatorTask:
    query = db.query(OperatorTask).filter(
        OperatorTask.source_type == source_type,
        OperatorTask.task_type == task_type,
        OperatorTask.status.notin_(list(TERMINAL_STATUSES)),
    )
    if source_id:
        query = query.filter(OperatorTask.source_id == source_id)
    if unresolved_event_id is not None:
        query = query.filter(OperatorTask.unresolved_event_id == unresolved_event_id)
    if webchat_conversation_id is not None:
        query = query.filter(OperatorTask.webchat_conversation_id == webchat_conversation_id)
    existing = query.order_by(OperatorTask.id.desc()).first()
    if existing:
        return existing

    row = OperatorTask(
        source_type=source_type[:40],
        source_id=source_id[:160] if source_id else None,
        ticket_id=ticket_id,
        webchat_conversation_id=webchat_conversation_id,
        unresolved_event_id=unresolved_event_id,
        task_type=task_type[:80],
        status="pending",
        priority=priority,
        reason_code=reason_code[:160] if reason_code else None,
        payload_json=_json_payload(payload),
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db.add(row)
    db.flush()
    if webchat_conversation_id and ticket_id:
        write_webchat_event(
            db,
            conversation_id=webchat_conversation_id,
            ticket_id=ticket_id,
            event_type="handoff.requested" if task_type in {"handoff", "customer_requested_human"} else "operator_task.created",
            payload={"operator_task_id": row.id, "task_type": task_type, "reason_code": reason_code},
        )
    return row


def project_openclaw_unresolved_events(db: Session, *, limit: int = 100) -> int:
    rows = (
        db.query(OpenClawUnresolvedEvent)
        .filter(OpenClawUnresolvedEvent.status == "pending")
        .order_by(OpenClawUnresolvedEvent.id.asc())
        .limit(max(1, min(limit, 500)))
        .all()
    )
    created = 0
    for event in rows:
        before = db.query(OperatorTask).filter(OperatorTask.unresolved_event_id == event.id, OperatorTask.status.notin_(list(TERMINAL_STATUSES))).first()
        if before:
            continue
        create_operator_task(
            db,
            source_type="openclaw",
            source_id=str(event.id),
            unresolved_event_id=event.id,
            task_type="bridge_unresolved",
            reason_code=event.event_type or "openclaw_unresolved",
            priority=50,
            payload={
                "session_key": event.session_key,
                "event_type": event.event_type,
                "recipient": event.recipient,
                "preferred_reply_contact": event.preferred_reply_contact,
                "last_error": event.last_error,
            },
        )
        created += 1
    return created


def project_webchat_handoff_tasks(db: Session, *, limit: int = 100) -> int:
    rows = (
        db.query(WebchatConversation, Ticket)
        .join(Ticket, Ticket.id == WebchatConversation.ticket_id)
        .filter(
            (Ticket.required_action.isnot(None))
            | (Ticket.conversation_state == ConversationState.human_review_required)
        )
        .order_by(WebchatConversation.id.asc())
        .limit(max(1, min(limit, 500)))
        .all()
    )
    created = 0
    for conversation, ticket in rows:
        before = (
            db.query(OperatorTask)
            .filter(
                OperatorTask.webchat_conversation_id == conversation.id,
                OperatorTask.task_type == "handoff",
                OperatorTask.status.notin_(list(TERMINAL_STATUSES)),
            )
            .first()
        )
        if before:
            continue
        create_operator_task(
            db,
            source_type="webchat",
            source_id=conversation.public_id,
            ticket_id=ticket.id,
            webchat_conversation_id=conversation.id,
            task_type="handoff",
            reason_code="ticket_required_action" if ticket.required_action else "human_review_required",
            priority=40,
            payload={
                "ticket_no": ticket.ticket_no,
                "required_action": ticket.required_action,
                "conversation_state": ticket.conversation_state.value if hasattr(ticket.conversation_state, "value") else str(ticket.conversation_state),
                "visitor_name": conversation.visitor_name,
                "visitor_email": conversation.visitor_email,
                "visitor_phone": conversation.visitor_phone,
                "origin": conversation.origin,
            },
        )
        created += 1
    return created


def list_operator_tasks(
    db: Session,
    *,
    status: str | None = None,
    source_type: str | None = None,
    task_type: str | None = None,
    cursor: int | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    query = db.query(OperatorTask)
    if status:
        query = query.filter(OperatorTask.status == status)
    if source_type:
        query = query.filter(OperatorTask.source_type == source_type)
    if task_type:
        query = query.filter(OperatorTask.task_type == task_type)
    if cursor:
        query = query.filter(OperatorTask.id < cursor)
    safe_limit = max(1, min(limit, 100))
    rows = query.order_by(OperatorTask.priority.asc(), OperatorTask.id.desc()).limit(safe_limit + 1).all()
    visible = rows[:safe_limit]
    return {
        "items": [serialize_operator_task(row) for row in visible],
        "next_cursor": rows[safe_limit].id if len(rows) > safe_limit else None,
    }


def transition_operator_task(
    db: Session,
    *,
    task_id: int,
    action: str,
    actor_id: int | None = None,
) -> OperatorTask:
    row = db.query(OperatorTask).filter(OperatorTask.id == task_id).first()
    if not row:
        raise ValueError("operator_task_not_found")
    now = utc_now()
    if action == "assign":
        row.status = "assigned"
        row.assignee_id = actor_id
    elif action in {"resolve", "drop", "replay"}:
        row.status = "resolved" if action == "resolve" else "dropped" if action == "drop" else "replayed"
        row.resolved_at = now
    else:
        raise ValueError("unsupported_operator_task_action")
    row.updated_at = now
    db.flush()
    if row.webchat_conversation_id and row.ticket_id:
        event_type = "handoff.assigned" if action == "assign" else "handoff.resolved" if action == "resolve" else f"handoff.{action}"
        write_webchat_event(
            db,
            conversation_id=row.webchat_conversation_id,
            ticket_id=row.ticket_id,
            event_type=event_type,
            payload={"operator_task_id": row.id, "action": action, "actor_id": actor_id},
        )
    return row


def create_webchat_handoff_task(db: Session, *, conversation: WebchatConversation, reason_code: str, payload: dict[str, Any] | None = None) -> OperatorTask:
    return create_operator_task(
        db,
        source_type="webchat",
        source_id=conversation.public_id,
        ticket_id=conversation.ticket_id,
        webchat_conversation_id=conversation.id,
        task_type="handoff",
        reason_code=reason_code,
        priority=40,
        payload=payload,
    )
