from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ..enums import ConversationState
from ..models import OpenClawUnresolvedEvent, Ticket
from ..operator_models import OperatorTask
from ..utils.time import utc_now
from ..webchat_models import WebchatConversation
from .audit_service import log_admin_audit
from .webchat_ai_turn_service import write_webchat_event

TERMINAL_STATUSES = {"resolved", "dropped", "replayed", "replay_failed", "cancelled"}
SENSITIVE_KEYS = {
    "session_key",
    "sessionkey",
    "visitor_email",
    "visitor_phone",
    "recipient",
    "preferred_reply_contact",
    "token",
    "visitor_token",
    "cookie",
    "authorization",
    "api_key",
    "apikey",
    "password",
    "secret",
    "prompt",
    "message",
    "body",
    "content",
}


class OperatorQueueError(RuntimeError):
    def __init__(self, status_code: int, code: str, detail: str) -> None:
        self.status_code = status_code
        self.code = code
        self.detail = detail
        super().__init__(code)


@dataclass
class ProjectResult:
    created: int = 0
    skipped_existing: int = 0


def _safe_note(note: str | None) -> str | None:
    if note is None:
        return None
    return note[:1000]


def _hash_preview(value: Any) -> dict[str, Any]:
    raw = "" if value is None else str(value)
    digest = hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return {"redacted": True, "length": len(raw), "sha256_prefix": digest}


def _safe_error_summary(value: Any) -> dict[str, Any]:
    if value in (None, ""):
        return {"redacted": True, "empty": True}
    raw = str(value)
    return {
        "redacted": True,
        "type": "error_summary",
        "length": len(raw),
        "sha256_prefix": hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:16],
    }


def sanitize_operator_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Return a bounded admin-only payload with sensitive values redacted."""

    if not payload:
        return {}

    def sanitize(value: Any, key: str = "") -> Any:
        key_l = key.lower()
        if key_l == "last_error":
            return _safe_error_summary(value)
        if key_l in SENSITIVE_KEYS or "token" in key_l or "secret" in key_l or "password" in key_l:
            return _hash_preview(value)
        if isinstance(value, dict):
            return {str(k)[:80]: sanitize(v, str(k)) for k, v in list(value.items())[:40]}
        if isinstance(value, list):
            return [sanitize(item, key) for item in value[:20]]
        if isinstance(value, str):
            if len(value) > 240:
                return {
                    "truncated": True,
                    "length": len(value),
                    "sha256_prefix": hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:16],
                }
            return value
        return value

    return sanitize(payload)


def _json_payload(payload: dict[str, Any] | None) -> str | None:
    safe = sanitize_operator_payload(payload)
    if not safe:
        return None
    return json.dumps(safe, ensure_ascii=False, default=str)


def _loads(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except Exception:
        return {
            "raw": {
                "truncated": True,
                "length": len(value),
                "sha256_prefix": hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:16],
            }
        }


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


def _task_snapshot(row: OperatorTask) -> dict[str, Any]:
    return {
        "id": row.id,
        "status": row.status,
        "assignee_id": row.assignee_id,
        "source_type": row.source_type,
        "source_id": row.source_id,
        "ticket_id": row.ticket_id,
        "webchat_conversation_id": row.webchat_conversation_id,
        "unresolved_event_id": row.unresolved_event_id,
    }


def _ticket_snapshot(ticket: Ticket | None) -> dict[str, Any] | None:
    if ticket is None:
        return None
    state = ticket.conversation_state
    return {
        "ticket_id": ticket.id,
        "required_action": ticket.required_action,
        "conversation_state": state.value if hasattr(state, "value") else str(state),
    }


def _unresolved_snapshot(row: OpenClawUnresolvedEvent | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "unresolved_event_id": row.id,
        "status": row.status,
        "replay_count": row.replay_count,
        "last_error": _safe_error_summary(row.last_error),
    }


def _active_query(db: Session, *, source_type: str, task_type: str):
    return db.query(OperatorTask).filter(
        OperatorTask.source_type == source_type,
        OperatorTask.task_type == task_type,
        OperatorTask.status.notin_(list(TERMINAL_STATUSES)),
    )


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
    note: str | None = None,
) -> tuple[OperatorTask, bool]:
    query = _active_query(db, source_type=source_type, task_type=task_type)
    if source_id:
        query = query.filter(OperatorTask.source_id == source_id)
    if unresolved_event_id is not None:
        query = query.filter(OperatorTask.unresolved_event_id == unresolved_event_id)
    if webchat_conversation_id is not None:
        query = query.filter(OperatorTask.webchat_conversation_id == webchat_conversation_id)
    existing = query.order_by(OperatorTask.id.desc()).first()
    if existing:
        return existing, False

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
        _write_webchat_handoff_event(
            db,
            row,
            action="requested" if task_type in {"handoff", "customer_requested_human"} else "created",
            actor_id=None,
            note=note,
        )
    return row, True


def _write_webchat_handoff_event(
    db: Session,
    row: OperatorTask,
    *,
    action: str,
    actor_id: int | None,
    note: str | None = None,
    replay_result: dict[str, Any] | None = None,
) -> None:
    if not row.webchat_conversation_id or not row.ticket_id:
        return
    payload: dict[str, Any] = {
        "operator_task_id": row.id,
        "action": action,
        "actor_id": actor_id,
    }
    if note:
        payload["note"] = _safe_note(note)
    if replay_result is not None:
        payload["replay_result"] = replay_result
    write_webchat_event(
        db,
        conversation_id=row.webchat_conversation_id,
        ticket_id=row.ticket_id,
        event_type=f"handoff.{action}",
        payload=payload,
    )


def _log_operator_audit(
    db: Session,
    *,
    actor_id: int | None,
    action: str,
    row: OperatorTask | None = None,
    target_id: int | None = None,
    old_value: dict[str, Any] | None = None,
    new_value: dict[str, Any] | None = None,
    note: str | None = None,
) -> None:
    new_payload = dict(new_value or {})
    if note:
        new_payload["note"] = _safe_note(note)
    if row is not None:
        new_payload.setdefault("task", _task_snapshot(row))
    log_admin_audit(
        db,
        actor_id=actor_id,
        action=f"operator_queue.{action}",
        target_type="operator_task" if row is not None else "operator_queue",
        target_id=row.id if row is not None else target_id,
        old_value=old_value,
        new_value=new_payload,
    )


def project_openclaw_unresolved_events(db: Session, *, limit: int = 100, actor_id: int | None = None, note: str | None = None) -> ProjectResult:
    rows = (
        db.query(OpenClawUnresolvedEvent)
        .filter(OpenClawUnresolvedEvent.status == "pending")
        .order_by(OpenClawUnresolvedEvent.id.asc())
        .limit(max(1, min(limit, 500)))
        .all()
    )
    result = ProjectResult()
    for event in rows:
        _, created = create_operator_task(
            db,
            source_type="openclaw",
            source_id=str(event.id),
            unresolved_event_id=event.id,
            task_type="bridge_unresolved",
            reason_code=event.event_type or "openclaw_unresolved",
            priority=50,
            payload={
                "source": event.source,
                "event_type": event.event_type,
                "session_key": event.session_key,
                "recipient": event.recipient,
                "preferred_reply_contact": event.preferred_reply_contact,
                "last_error": event.last_error,
            },
            note=note,
        )
        if created:
            result.created += 1
        else:
            result.skipped_existing += 1
    if result.created or result.skipped_existing:
        _log_operator_audit(
            db,
            actor_id=actor_id,
            action="project",
            old_value=None,
            new_value={"source_type": "openclaw", "created": result.created, "skipped_existing": result.skipped_existing},
            note=note,
        )
    return result


def project_webchat_handoff_tasks(db: Session, *, limit: int = 100, actor_id: int | None = None, note: str | None = None) -> ProjectResult:
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
    result = ProjectResult()
    for conversation, ticket in rows:
        _, created = create_operator_task(
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
            note=note,
        )
        if created:
            result.created += 1
        else:
            result.skipped_existing += 1
    if result.created or result.skipped_existing:
        _log_operator_audit(
            db,
            actor_id=actor_id,
            action="project",
            old_value=None,
            new_value={"source_type": "webchat", "created": result.created, "skipped_existing": result.skipped_existing},
            note=note,
        )
    return result


def project_operator_queue(db: Session, *, actor_id: int | None = None, note: str | None = None) -> dict[str, int]:
    openclaw = project_openclaw_unresolved_events(db, actor_id=actor_id, note=note)
    webchat = project_webchat_handoff_tasks(db, actor_id=actor_id, note=note)
    return {
        "projected_openclaw_unresolved": openclaw.created,
        "projected_webchat_handoff": webchat.created,
        "created_total": openclaw.created + webchat.created,
        "skipped_existing": openclaw.skipped_existing + webchat.skipped_existing,
    }


def encode_operator_cursor(*, priority: int, task_id: int) -> str:
    raw = json.dumps({"priority": priority, "id": task_id}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_operator_cursor(cursor: str | None) -> tuple[int, int] | None:
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        parsed = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        return int(parsed["priority"]), int(parsed["id"])
    except Exception as exc:
        raise OperatorQueueError(400, "invalid_cursor", "invalid operator queue cursor") from exc


def list_operator_tasks(
    db: Session,
    *,
    status: str | None = None,
    source_type: str | None = None,
    task_type: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    query = db.query(OperatorTask)
    if status:
        query = query.filter(OperatorTask.status == status)
    if source_type:
        query = query.filter(OperatorTask.source_type == source_type)
    if task_type:
        query = query.filter(OperatorTask.task_type == task_type)
    decoded_cursor = decode_operator_cursor(cursor)
    if decoded_cursor:
        cursor_priority, cursor_id = decoded_cursor
        query = query.filter(
            or_(
                OperatorTask.priority > cursor_priority,
                and_(OperatorTask.priority == cursor_priority, OperatorTask.id < cursor_id),
            )
        )
    safe_limit = max(1, min(limit, 100))
    rows = query.order_by(OperatorTask.priority.asc(), OperatorTask.id.desc()).limit(safe_limit + 1).all()
    visible = rows[:safe_limit]
    next_cursor = None
    if len(rows) > safe_limit:
        last = visible[-1]
        next_cursor = encode_operator_cursor(priority=last.priority, task_id=last.id)
    return {
        "items": [serialize_operator_task(row) for row in visible],
        "next_cursor": next_cursor,
        "filters": {"status": status, "source_type": source_type, "task_type": task_type},
    }


def _get_task(db: Session, task_id: int) -> OperatorTask:
    row = db.query(OperatorTask).filter(OperatorTask.id == task_id).first()
    if not row:
        raise OperatorQueueError(404, "operator_task_not_found", "operator task not found")
    return row


def _close_webchat_source(db: Session, row: OperatorTask, *, action: str, actor_id: int | None, note: str | None) -> dict[str, Any]:
    if not row.ticket_id:
        return {}
    ticket = db.query(Ticket).filter(Ticket.id == row.ticket_id).first()
    old = _ticket_snapshot(ticket)
    if ticket is None:
        return {"old": old, "new": None}
    ticket.required_action = None
    if ticket.conversation_state == ConversationState.human_review_required:
        ticket.conversation_state = ConversationState.human_owned
    ticket.updated_at = utc_now()
    new = _ticket_snapshot(ticket)
    _write_webchat_handoff_event(db, row, action=action, actor_id=actor_id, note=note)
    return {"old": old, "new": new}


def _close_openclaw_source(db: Session, row: OperatorTask, *, status: str) -> dict[str, Any]:
    if not row.unresolved_event_id:
        return {}
    event_row = db.query(OpenClawUnresolvedEvent).filter(OpenClawUnresolvedEvent.id == row.unresolved_event_id).first()
    old = _unresolved_snapshot(event_row)
    if event_row is None:
        return {"old": old, "new": None}
    event_row.status = status
    if status in {"resolved", "dropped", "replayed"}:
        event_row.last_error = None
    event_row.updated_at = utc_now()
    new = _unresolved_snapshot(event_row)
    return {"old": old, "new": new}


def transition_operator_task(
    db: Session,
    *,
    task_id: int,
    action: str,
    actor_id: int | None = None,
    note: str | None = None,
) -> OperatorTask:
    if action not in {"assign", "resolve", "drop"}:
        raise OperatorQueueError(400, "unsupported_operator_task_action", "unsupported operator task action")
    row = _get_task(db, task_id)
    old_task = _task_snapshot(row)
    now = utc_now()
    source_transition: dict[str, Any] = {}
    if action == "assign":
        row.status = "assigned"
        row.assignee_id = actor_id
        _write_webchat_handoff_event(db, row, action="assigned", actor_id=actor_id, note=note)
    else:
        row.status = "resolved" if action == "resolve" else "dropped"
        row.resolved_at = now
        if row.source_type == "webchat":
            source_transition = _close_webchat_source(db, row, action="resolved" if action == "resolve" else "dropped", actor_id=actor_id, note=note)
        elif row.source_type == "openclaw":
            source_transition = _close_openclaw_source(db, row, status=row.status)
    row.updated_at = now
    db.flush()
    _log_operator_audit(
        db,
        actor_id=actor_id,
        action=action,
        row=row,
        old_value={"task": old_task, "source": source_transition.get("old")},
        new_value={"task": _task_snapshot(row), "source": source_transition.get("new")},
        note=note,
    )
    return row


def replay_operator_task(
    db: Session,
    *,
    task_id: int,
    actor_id: int | None = None,
    note: str | None = None,
    replay_func: Callable[..., bool],
) -> tuple[OperatorTask, dict[str, Any]]:
    row = _get_task(db, task_id)
    if not row.unresolved_event_id:
        raise OperatorQueueError(404, "unresolved_event_missing", "unresolved event missing")
    event_row = db.query(OpenClawUnresolvedEvent).filter(OpenClawUnresolvedEvent.id == row.unresolved_event_id).first()
    if event_row is None:
        raise OperatorQueueError(404, "unresolved_event_missing", "unresolved event missing")

    old_task = _task_snapshot(row)
    old_source = _unresolved_snapshot(event_row)
    try:
        ok = bool(replay_func(db, row=event_row))
    except Exception as exc:
        now = utc_now()
        row.status = "replay_failed"
        row.resolved_at = now
        row.updated_at = now
        event_row.status = "replay_failed"
        event_row.last_error = type(exc).__name__
        event_row.updated_at = now
        db.flush()
        safe_result = {"ok": False, "status": "replay_failed", "error_code": type(exc).__name__}
        _log_operator_audit(
            db,
            actor_id=actor_id,
            action="replay_failed",
            row=row,
            old_value={"task": old_task, "source": old_source},
            new_value={"task": _task_snapshot(row), "source": _unresolved_snapshot(event_row), "replay_result": safe_result},
            note=note,
        )
        raise OperatorQueueError(409, "replay_failed", "replay failed") from exc

    now = utc_now()
    if ok:
        row.status = "replayed"
        row.resolved_at = now
        event_row.status = "replayed"
        event_row.last_error = None
        event_action = "replayed"
        replay_result = {"ok": True, "status": "replayed"}
    else:
        row.status = "replay_failed"
        row.resolved_at = now
        event_row.status = "replay_failed"
        event_action = "replay_failed"
        replay_result = {"ok": False, "status": "replay_failed"}
    row.updated_at = now
    event_row.updated_at = now
    db.flush()

    _log_operator_audit(
        db,
        actor_id=actor_id,
        action=event_action,
        row=row,
        old_value={"task": old_task, "source": old_source},
        new_value={"task": _task_snapshot(row), "source": _unresolved_snapshot(event_row), "replay_result": replay_result},
        note=note,
    )
    if row.webchat_conversation_id and row.ticket_id:
        _close_webchat_source(db, row, action=event_action, actor_id=actor_id, note=note)
    if not ok:
        raise OperatorQueueError(409, "replay_failed", "replay failed")
    return row, replay_result


def create_webchat_handoff_task(
    db: Session,
    *,
    conversation: WebchatConversation,
    reason_code: str,
    payload: dict[str, Any] | None = None,
) -> OperatorTask:
    row, _ = create_operator_task(
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
    return row
