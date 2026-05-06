from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from ..enums import ConversationState
from ..models import OpenClawUnresolvedEvent, Ticket
from ..operator_models import OperatorTask
from ..utils.time import utc_now
from ..webchat_models import WebchatConversation
from .audit_service import log_admin_audit
from .webchat_ai_turn_service import write_webchat_event

OPERATOR_TASK_PENDING = "pending"
OPERATOR_TASK_ASSIGNED = "assigned"
OPERATOR_TASK_REPLAYING = "replaying"
OPERATOR_TASK_RESOLVED = "resolved"
OPERATOR_TASK_DROPPED = "dropped"
OPERATOR_TASK_REPLAYED = "replayed"
OPERATOR_TASK_FAILED = "failed"

TERMINAL_STATUSES = {OPERATOR_TASK_RESOLVED, OPERATOR_TASK_DROPPED, OPERATOR_TASK_REPLAYED}


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


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _snapshot_task(row: OperatorTask) -> dict[str, Any]:
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
        "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
    }


def _snapshot_ticket(ticket: Ticket) -> dict[str, Any]:
    return {
        "id": ticket.id,
        "ticket_no": ticket.ticket_no,
        "required_action": ticket.required_action,
        "conversation_state": _enum_value(ticket.conversation_state),
    }


def _snapshot_unresolved_event(row: OpenClawUnresolvedEvent) -> dict[str, Any]:
    return {
        "id": row.id,
        "status": row.status,
        "last_error": row.last_error,
        "replay_count": row.replay_count,
        "session_key": row.session_key,
        "event_type": row.event_type,
    }


def _safe_log_admin_audit(
    db: Session,
    *,
    actor_id: int | None,
    action: str,
    target_type: str,
    target_id: int | None,
    old_value: dict[str, Any] | None = None,
    new_value: dict[str, Any] | None = None,
    note: str | None = None,
) -> None:
    # Unit tests in this PR use a deliberately tiny fake DB without add().
    # Real SQLAlchemy sessions must write the audit row.
    if not hasattr(db, "add"):
        return
    enriched_new_value = dict(new_value or {})
    if note:
        enriched_new_value["note"] = note
    log_admin_audit(
        db,
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        old_value=old_value,
        new_value=enriched_new_value,
    )


def _record_operator_note(
    row: OperatorTask,
    *,
    action: str,
    actor_id: int | None,
    note: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    payload = _loads(row.payload_json)
    history = payload.get("operator_history")
    if not isinstance(history, list):
        history = []
    entry: dict[str, Any] = {
        "action": action,
        "actor_id": actor_id,
        "at": utc_now().isoformat(),
    }
    if note:
        entry["note"] = note
        payload["last_operator_note"] = note
    if extra:
        entry.update(extra)
        payload["last_operator_result"] = extra
    history.append(entry)
    payload["operator_history"] = history[-20:]
    row.payload_json = _json_payload(payload)


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
        status=OPERATOR_TASK_PENDING,
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
    rows = query.order_by(OperatorTask.id.desc()).limit(safe_limit + 1).all()
    visible = rows[:safe_limit]
    return {
        "items": [serialize_operator_task(row) for row in visible],
        "next_cursor": visible[-1].id if len(rows) > safe_limit and visible else None,
    }


def _close_webchat_source(
    db: Session,
    *,
    row: OperatorTask,
    action: str,
    actor_id: int | None,
    note: str | None,
) -> dict[str, Any] | None:
    if row.source_type != "webchat" or row.task_type != "handoff" or not row.ticket_id:
        return None

    ticket = db.query(Ticket).filter(Ticket.id == row.ticket_id).first()
    if ticket is None or not hasattr(ticket, "required_action"):
        return {"webchat_source": "missing", "ticket_id": row.ticket_id}

    old = _snapshot_ticket(ticket)
    changed = False
    if ticket.required_action is not None:
        ticket.required_action = None
        changed = True
    if ticket.conversation_state == ConversationState.human_review_required:
        # Keep the conversation on the human side after an operator closes the queue task.
        # This is safer than reactivating AI after a human-review signal.
        ticket.conversation_state = ConversationState.human_owned
        changed = True
    if changed:
        ticket.updated_at = utc_now()

    new = _snapshot_ticket(ticket)
    if row.webchat_conversation_id:
        write_webchat_event(
            db,
            conversation_id=row.webchat_conversation_id,
            ticket_id=row.ticket_id,
            event_type=f"handoff.source_{action}",
            payload={
                "operator_task_id": row.id,
                "action": action,
                "actor_id": actor_id,
                "note": note,
                "old": old,
                "new": new,
            },
        )
    return {"webchat_source_old": old, "webchat_source_new": new}


def _close_openclaw_source(
    db: Session,
    *,
    row: OperatorTask,
    action: str,
    note: str | None,
) -> dict[str, Any] | None:
    if row.source_type != "openclaw" or row.task_type != "bridge_unresolved":
        return None
    if not row.unresolved_event_id:
        raise ValueError("unresolved_event_missing")

    event_row = db.query(OpenClawUnresolvedEvent).filter(OpenClawUnresolvedEvent.id == row.unresolved_event_id).first()
    if event_row is None or not hasattr(event_row, "status"):
        raise ValueError("unresolved_event_missing")

    old = _snapshot_unresolved_event(event_row)
    if action in {"resolve", "replay"}:
        event_row.status = "resolved"
        event_row.last_error = None
    elif action == "drop":
        event_row.status = "dropped"
        event_row.last_error = note or "Dropped by operator"
    event_row.updated_at = utc_now()
    new = _snapshot_unresolved_event(event_row)
    return {"openclaw_source_old": old, "openclaw_source_new": new}


def transition_operator_task(
    db: Session,
    *,
    task_id: int,
    action: str,
    actor_id: int | None = None,
    note: str | None = None,
) -> OperatorTask:
    row = db.query(OperatorTask).filter(OperatorTask.id == task_id).first()
    if not row:
        raise ValueError("operator_task_not_found")
    if action not in {"assign", "resolve", "drop", "replay"}:
        raise ValueError("unsupported_operator_task_action")

    old_value = _snapshot_task(row)
    now = utc_now()
    source_update: dict[str, Any] | None = None

    if action == "assign":
        row.status = OPERATOR_TASK_ASSIGNED
        row.assignee_id = actor_id
    elif action in {"resolve", "drop", "replay"}:
        if action == "resolve":
            row.status = OPERATOR_TASK_RESOLVED
            source_update = _close_webchat_source(db, row=row, action=action, actor_id=actor_id, note=note)
            source_update = {**(source_update or {}), **(_close_openclaw_source(db, row=row, action=action, note=note) or {})}
        elif action == "drop":
            row.status = OPERATOR_TASK_DROPPED
            source_update = _close_webchat_source(db, row=row, action=action, actor_id=actor_id, note=note)
            source_update = {**(source_update or {}), **(_close_openclaw_source(db, row=row, action=action, note=note) or {})}
        else:
            row.status = OPERATOR_TASK_REPLAYED
            source_update = _close_openclaw_source(db, row=row, action=action, note=note)
        row.resolved_at = now

    _record_operator_note(row, action=action, actor_id=actor_id, note=note, extra=source_update)
    row.updated_at = now
    db.flush()

    if row.webchat_conversation_id and row.ticket_id:
        event_type = "handoff.assigned" if action == "assign" else "handoff.resolved" if action == "resolve" else f"handoff.{action}"
        write_webchat_event(
            db,
            conversation_id=row.webchat_conversation_id,
            ticket_id=row.ticket_id,
            event_type=event_type,
            payload={"operator_task_id": row.id, "action": action, "actor_id": actor_id, "note": note},
        )

    new_value = _snapshot_task(row)
    if source_update:
        new_value["source_update"] = source_update
    _safe_log_admin_audit(
        db,
        actor_id=actor_id,
        action=f"operator_task.{action}",
        target_type="operator_task",
        target_id=row.id,
        old_value=old_value,
        new_value=new_value,
        note=note,
    )
    return row


def mark_operator_task_replaying(
    db: Session,
    *,
    row: OperatorTask,
    actor_id: int | None = None,
    note: str | None = None,
) -> OperatorTask:
    row.status = OPERATOR_TASK_REPLAYING
    row.resolved_at = None
    row.updated_at = utc_now()
    _record_operator_note(row, action="replay_start", actor_id=actor_id, note=note)
    db.flush()
    return row


def mark_operator_task_replay_failed(
    db: Session,
    *,
    row: OperatorTask,
    event_row: OpenClawUnresolvedEvent | None = None,
    actor_id: int | None = None,
    note: str | None = None,
    error: str | None = None,
) -> OperatorTask:
    old_value = _snapshot_task(row)
    row.status = OPERATOR_TASK_FAILED
    row.resolved_at = None
    row.updated_at = utc_now()
    source_update = None
    if event_row is not None and hasattr(event_row, "status"):
        if event_row.status not in {"resolved", "dropped"}:
            event_row.status = "failed"
        if error and not event_row.last_error:
            event_row.last_error = error
        event_row.updated_at = utc_now()
        source_update = {"openclaw_source_new": _snapshot_unresolved_event(event_row)}
    failure_payload = {
        "replay_result": False,
        "error": error or (event_row.last_error if event_row is not None else None) or "openclaw_replay_failed",
    }
    if source_update:
        failure_payload.update(source_update)
    _record_operator_note(row, action="replay_failed", actor_id=actor_id, note=note, extra=failure_payload)
    db.flush()
    new_value = _snapshot_task(row)
    if source_update:
        new_value["source_update"] = source_update
    _safe_log_admin_audit(
        db,
        actor_id=actor_id,
        action="operator_task.replay",
        target_type="operator_task",
        target_id=row.id,
        old_value=old_value,
        new_value=new_value,
        note=note,
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
