from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import String, and_, case, cast, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Tenant, Ticket
from ..operator_models import OperatorTask
from ..utils.time import utc_now
from ..webchat_models import WebchatConversation, WebchatHandoffRequest
from .audit_service import log_admin_audit

TERMINAL_STATUSES = {
    "resolved",
    "dropped",
    "replayed",
    "replay_failed",
    "cancelled",
}
OPEN_HANDOFF_STATUSES = {"requested", "accepted"}
HANDOFF_PROJECTION_SOURCE = "webchat_handoff"
HANDOFF_PROJECTION_SCHEMA = "nexus.operator-task.webchat-handoff.v2"
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
    retired: int = 0


def _safe_note(note: str | None) -> str | None:
    return note[:1000] if note is not None else None


def _hash_preview(value: Any) -> dict[str, Any]:
    raw = "" if value is None else str(value)
    digest = hashlib.sha256(
        raw.encode("utf-8", errors="ignore")
    ).hexdigest()[:16]
    return {
        "redacted": True,
        "length": len(raw),
        "sha256_prefix": digest,
    }


def _safe_error_summary(value: Any) -> dict[str, Any]:
    if value in (None, ""):
        return {"redacted": True, "empty": True}
    raw = str(value)
    return {
        "redacted": True,
        "type": "error_summary",
        "length": len(raw),
        "sha256_prefix": hashlib.sha256(
            raw.encode("utf-8", errors="ignore")
        ).hexdigest()[:16],
    }


def sanitize_operator_payload(
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return a bounded admin-only payload with sensitive values redacted."""

    if not payload:
        return {}

    def sanitize(value: Any, key: str = "") -> Any:
        key_l = key.lower()
        if key_l == "last_error":
            return _safe_error_summary(value)
        if (
            key_l in SENSITIVE_KEYS
            or "token" in key_l
            or "secret" in key_l
            or "password" in key_l
        ):
            return _hash_preview(value)
        if isinstance(value, dict):
            return {
                str(k)[:80]: sanitize(v, str(k))
                for k, v in list(value.items())[:40]
            }
        if isinstance(value, list):
            return [sanitize(item, key) for item in value[:20]]
        if isinstance(value, str) and len(value) > 240:
            return {
                "truncated": True,
                "length": len(value),
                "sha256_prefix": hashlib.sha256(
                    value.encode("utf-8", errors="ignore")
                ).hexdigest()[:16],
            }
        return value

    result = sanitize(payload)
    return result if isinstance(result, dict) else {}


def _json_payload(payload: dict[str, Any] | None) -> str | None:
    safe = sanitize_operator_payload(payload)
    return json.dumps(safe, ensure_ascii=False, default=str) if safe else None


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
                "sha256_prefix": hashlib.sha256(
                    value.encode("utf-8", errors="ignore")
                ).hexdigest()[:16],
            }
        }


def serialize_operator_task(row: OperatorTask) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "source_type": row.source_type,
        "source_id": row.source_id,
        "source_version": row.source_version,
        "projection_schema": row.projection_schema,
        "ticket_id": row.ticket_id,
        "webchat_conversation_id": row.webchat_conversation_id,
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
        "tenant_id": row.tenant_id,
        "status": row.status,
        "assignee_id": row.assignee_id,
        "source_type": row.source_type,
        "source_id": row.source_id,
        "source_version": row.source_version,
        "projection_schema": row.projection_schema,
        "ticket_id": row.ticket_id,
        "webchat_conversation_id": row.webchat_conversation_id,
    }


def _tenant_id_for_conversation(
    db: Session,
    conversation: WebchatConversation,
) -> int:
    tenant_key = str(conversation.tenant_key or "").strip().lower()
    if not tenant_key or tenant_key == "default":
        raise OperatorQueueError(
            409,
            "operator_task_conversation_tenant_missing",
            "conversation has no production Tenant ownership",
        )
    tenant = (
        db.query(Tenant)
        .filter(
            Tenant.tenant_key == tenant_key,
            Tenant.is_active.is_(True),
        )
        .first()
    )
    if tenant is None:
        raise OperatorQueueError(
            409,
            "operator_task_conversation_tenant_missing",
            "conversation Tenant is unavailable",
        )
    return int(tenant.id)


def resolve_operator_task_tenant_id(
    db: Session,
    *,
    tenant_id: int | None = None,
    ticket_id: int | None = None,
    webchat_conversation_id: int | None = None,
) -> int:
    candidates: set[int] = set()
    if tenant_id is not None:
        tenant = db.get(Tenant, int(tenant_id))
        if tenant is None or not tenant.is_active:
            raise OperatorQueueError(
                409,
                "operator_task_tenant_unavailable",
                "operator task Tenant is unavailable",
            )
        candidates.add(int(tenant.id))
    if ticket_id is not None:
        ticket = db.get(Ticket, int(ticket_id))
        if ticket is None or ticket.tenant_id is None:
            raise OperatorQueueError(
                409,
                "operator_task_ticket_tenant_missing",
                "operator task Ticket has no Tenant ownership",
            )
        candidates.add(int(ticket.tenant_id))
    if webchat_conversation_id is not None:
        conversation = db.get(
            WebchatConversation,
            int(webchat_conversation_id),
        )
        if conversation is None:
            raise OperatorQueueError(
                409,
                "operator_task_conversation_missing",
                "operator task Conversation is unavailable",
            )
        candidates.add(_tenant_id_for_conversation(db, conversation))
    if not candidates:
        raise OperatorQueueError(
            409,
            "operator_task_tenant_required",
            "operator task requires explicit or source-derived Tenant ownership",
        )
    if len(candidates) != 1:
        raise OperatorQueueError(
            409,
            "operator_task_tenant_conflict",
            "operator task sources belong to different Tenants",
        )
    return candidates.pop()


def _active_query(
    db: Session,
    *,
    tenant_id: int,
    source_type: str,
    task_type: str,
):
    return db.query(OperatorTask).filter(
        OperatorTask.tenant_id == tenant_id,
        OperatorTask.source_type == source_type,
        OperatorTask.task_type == task_type,
        OperatorTask.status.notin_(list(TERMINAL_STATUSES)),
    )


def _find_existing_active_task(
    db: Session,
    *,
    tenant_id: int,
    source_type: str,
    task_type: str,
    source_id: str | None = None,
    webchat_conversation_id: int | None = None,
) -> OperatorTask | None:
    query = _active_query(
        db,
        tenant_id=tenant_id,
        source_type=source_type,
        task_type=task_type,
    )
    identities = []
    if source_id:
        identities.append(OperatorTask.source_id == source_id)
    if webchat_conversation_id is not None:
        identities.append(
            OperatorTask.webchat_conversation_id == webchat_conversation_id
        )
    if not identities:
        return None
    return (
        query.filter(or_(*identities))
        .order_by(OperatorTask.id.desc())
        .first()
    )


def _ensure_task_mutable(row: OperatorTask) -> None:
    if row.status in TERMINAL_STATUSES:
        raise OperatorQueueError(
            409,
            "operator_task_terminal",
            "operator task is terminal",
        )


def _is_source_owned_projection(row: OperatorTask) -> bool:
    return bool(
        row.source_type == HANDOFF_PROJECTION_SOURCE
        or (
            row.task_type == "handoff"
            and row.webchat_conversation_id is not None
        )
    )


def _refresh_existing_task(
    row: OperatorTask,
    *,
    tenant_id: int,
    source_id: str | None = None,
    source_version: int | None = None,
    projection_schema: str | None = None,
    ticket_id: int | None = None,
    webchat_conversation_id: int | None = None,
    reason_code: str | None = None,
    priority: int | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    if row.tenant_id != tenant_id:
        raise OperatorQueueError(
            409,
            "operator_task_tenant_conflict",
            "operator task cannot move between Tenants",
        )
    if source_id:
        row.source_id = source_id[:160]
    if source_version is not None:
        row.source_version = source_version
    if projection_schema:
        row.projection_schema = projection_schema[:80]
    if ticket_id is not None:
        row.ticket_id = ticket_id
    if webchat_conversation_id is not None:
        row.webchat_conversation_id = webchat_conversation_id
    if reason_code:
        row.reason_code = reason_code[:160]
    if priority is not None:
        row.priority = priority
    if payload is not None:
        row.payload_json = _json_payload(payload)
    row.updated_at = utc_now()


def create_operator_task(
    db: Session,
    *,
    source_type: str,
    task_type: str,
    tenant_id: int | None = None,
    reason_code: str | None = None,
    source_id: str | None = None,
    source_version: int | None = None,
    projection_schema: str = "nexus.operator-task-projection.v1",
    ticket_id: int | None = None,
    webchat_conversation_id: int | None = None,
    priority: int = 100,
    payload: dict[str, Any] | None = None,
    note: str | None = None,
) -> tuple[OperatorTask, bool]:
    del note
    resolved_tenant_id = resolve_operator_task_tenant_id(
        db,
        tenant_id=tenant_id,
        ticket_id=ticket_id,
        webchat_conversation_id=webchat_conversation_id,
    )
    existing = _find_existing_active_task(
        db,
        tenant_id=resolved_tenant_id,
        source_type=source_type,
        task_type=task_type,
        source_id=source_id,
        webchat_conversation_id=webchat_conversation_id,
    )
    if existing:
        _refresh_existing_task(
            existing,
            tenant_id=resolved_tenant_id,
            source_id=source_id,
            source_version=source_version,
            projection_schema=projection_schema,
            ticket_id=ticket_id,
            webchat_conversation_id=webchat_conversation_id,
            reason_code=reason_code,
            priority=priority,
            payload=payload,
        )
        return existing, False

    row = OperatorTask(
        tenant_id=resolved_tenant_id,
        source_type=source_type[:40],
        source_id=source_id[:160] if source_id else None,
        source_version=source_version,
        projection_schema=projection_schema[:80],
        ticket_id=ticket_id,
        webchat_conversation_id=webchat_conversation_id,
        task_type=task_type[:80],
        status="pending",
        priority=priority,
        reason_code=reason_code[:160] if reason_code else None,
        payload_json=_json_payload(payload),
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        existing = _find_existing_active_task(
            db,
            tenant_id=resolved_tenant_id,
            source_type=source_type,
            task_type=task_type,
            source_id=source_id,
            webchat_conversation_id=webchat_conversation_id,
        )
        if existing:
            _refresh_existing_task(
                existing,
                tenant_id=resolved_tenant_id,
                source_id=source_id,
                source_version=source_version,
                projection_schema=projection_schema,
                ticket_id=ticket_id,
                webchat_conversation_id=webchat_conversation_id,
                reason_code=reason_code,
                priority=priority,
                payload=payload,
            )
            return existing, False
        raise OperatorQueueError(
            409,
            "operator_task_conflict",
            "operator task already exists in this Tenant",
        ) from exc
    return row, True


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


def _handoff_task_status(request_row: WebchatHandoffRequest) -> str:
    return "assigned" if request_row.status == "accepted" else "pending"


def _handoff_task_payload(
    request_row: WebchatHandoffRequest,
    conversation: WebchatConversation,
    ticket: Ticket | None,
) -> dict[str, Any]:
    return {
        "handoff_request_id": request_row.id,
        "source_version": request_row.lock_version,
        "handoff_status": request_row.status,
        "source": request_row.source,
        "trigger_type": request_row.trigger_type,
        "reason_code": request_row.reason_code,
        "recommended_agent_action": request_row.recommended_agent_action,
        "ticket_no": ticket.ticket_no if ticket is not None else None,
        "visitor_name": conversation.visitor_name,
        "origin": conversation.origin,
    }


def _project_handoff_request(
    db: Session,
    *,
    request_row: WebchatHandoffRequest,
    conversation: WebchatConversation,
    ticket: Ticket | None,
) -> tuple[OperatorTask, bool]:
    if request_row.status not in OPEN_HANDOFF_STATUSES:
        raise OperatorQueueError(
            409,
            "handoff_projection_source_terminal",
            "handoff source is terminal",
        )
    row, created = create_operator_task(
        db,
        tenant_id=_tenant_id_for_conversation(db, conversation),
        source_type=HANDOFF_PROJECTION_SOURCE,
        source_id=str(request_row.id),
        source_version=request_row.lock_version,
        projection_schema=HANDOFF_PROJECTION_SCHEMA,
        ticket_id=request_row.ticket_id,
        webchat_conversation_id=request_row.conversation_id,
        task_type="handoff",
        reason_code=request_row.reason_code or request_row.trigger_type,
        priority=40,
        payload=_handoff_task_payload(request_row, conversation, ticket),
    )
    row.status = _handoff_task_status(request_row)
    row.assignee_id = request_row.assigned_agent_id
    row.resolved_at = None
    row.updated_at = utc_now()
    return row, created


def _retire_stale_handoff_projections(db: Session) -> int:
    rows = (
        db.query(OperatorTask)
        .filter(
            OperatorTask.source_type == HANDOFF_PROJECTION_SOURCE,
            OperatorTask.task_type == "handoff",
            OperatorTask.status.notin_(list(TERMINAL_STATUSES)),
        )
        .order_by(OperatorTask.id.asc())
        .limit(5000)
        .all()
    )
    retired = 0
    now = utc_now()
    for row in rows:
        try:
            request_id = int(row.source_id or "")
        except ValueError:
            request_id = 0
        request_row = (
            db.get(WebchatHandoffRequest, request_id) if request_id else None
        )
        if request_row is not None and request_row.status in OPEN_HANDOFF_STATUSES:
            continue
        row.status = (
            "cancelled"
            if request_row is not None
            and request_row.status in {"cancelled", "expired", "resumed_ai"}
            else "resolved"
        )
        row.reason_code = (
            f"source_{request_row.status}"
            if request_row is not None
            else "source_missing"
        )
        row.assignee_id = (
            request_row.assigned_agent_id
            if request_row is not None
            else row.assignee_id
        )
        row.source_version = (
            request_row.lock_version
            if request_row is not None
            else row.source_version
        )
        row.resolved_at = (
            request_row.closed_at if request_row is not None else now
        )
        row.updated_at = now
        retired += 1
    return retired


def _handoff_projection_candidates(db: Session, *, limit: int):
    expected_status = case(
        (WebchatHandoffRequest.status == "accepted", "assigned"),
        else_="pending",
    )
    active_join = and_(
        OperatorTask.tenant_id == Tenant.id,
        OperatorTask.source_type == HANDOFF_PROJECTION_SOURCE,
        OperatorTask.task_type == "handoff",
        OperatorTask.status.notin_(list(TERMINAL_STATUSES)),
        OperatorTask.source_id == cast(WebchatHandoffRequest.id, String),
    )
    return (
        db.query(WebchatHandoffRequest, WebchatConversation, Ticket)
        .join(
            WebchatConversation,
            WebchatConversation.id == WebchatHandoffRequest.conversation_id,
        )
        .join(Tenant, Tenant.tenant_key == WebchatConversation.tenant_key)
        .outerjoin(Ticket, Ticket.id == WebchatHandoffRequest.ticket_id)
        .outerjoin(OperatorTask, active_join)
        .filter(
            WebchatHandoffRequest.status.in_(OPEN_HANDOFF_STATUSES),
            Tenant.is_active.is_(True),
            or_(
                OperatorTask.id.is_(None),
                OperatorTask.source_version.is_distinct_from(
                    WebchatHandoffRequest.lock_version
                ),
                OperatorTask.projection_schema != HANDOFF_PROJECTION_SCHEMA,
                OperatorTask.status != expected_status,
                OperatorTask.assignee_id.is_distinct_from(
                    WebchatHandoffRequest.assigned_agent_id
                ),
            ),
        )
        .order_by(
            WebchatHandoffRequest.requested_at.asc(),
            WebchatHandoffRequest.id.asc(),
        )
        .limit(max(1, min(int(limit), 500)))
        .all()
    )


def project_webchat_handoff_tasks(
    db: Session,
    *,
    limit: int = 100,
    actor_id: int | None = None,
    note: str | None = None,
) -> ProjectResult:
    rows = _handoff_projection_candidates(db, limit=limit)
    result = ProjectResult()
    for request_row, conversation, ticket in rows:
        _, created = _project_handoff_request(
            db,
            request_row=request_row,
            conversation=conversation,
            ticket=ticket,
        )
        if created:
            result.created += 1
        else:
            result.skipped_existing += 1
    result.retired = _retire_stale_handoff_projections(db)
    if result.created or result.skipped_existing or result.retired:
        _log_operator_audit(
            db,
            actor_id=actor_id,
            action="project",
            old_value=None,
            new_value={
                "source_type": HANDOFF_PROJECTION_SOURCE,
                "created": result.created,
                "refreshed": result.skipped_existing,
                "retired": result.retired,
            },
            note=note,
        )
    return result


def project_operator_queue(
    db: Session,
    *,
    actor_id: int | None = None,
    note: str | None = None,
) -> dict[str, int]:
    handoff = project_webchat_handoff_tasks(
        db,
        actor_id=actor_id,
        note=note,
    )
    return {
        "projected_webchat_handoff": handoff.created,
        "created_total": handoff.created,
        "skipped_existing": handoff.skipped_existing,
    }


def encode_operator_cursor(*, priority: int, task_id: int) -> str:
    raw = json.dumps(
        {"priority": priority, "id": task_id},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_operator_cursor(cursor: str | None) -> tuple[int, int] | None:
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        parsed = json.loads(
            base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        )
        return int(parsed["priority"]), int(parsed["id"])
    except Exception as exc:
        raise OperatorQueueError(
            400,
            "invalid_cursor",
            "invalid operator queue cursor",
        ) from exc


def list_operator_tasks(
    db: Session,
    *,
    tenant_id: int,
    status: str | None = None,
    source_type: str | None = None,
    task_type: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    query = db.query(OperatorTask).filter(OperatorTask.tenant_id == tenant_id)
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
                and_(
                    OperatorTask.priority == cursor_priority,
                    OperatorTask.id < cursor_id,
                ),
            )
        )
    safe_limit = max(1, min(limit, 100))
    rows = (
        query.order_by(OperatorTask.priority.asc(), OperatorTask.id.desc())
        .limit(safe_limit + 1)
        .all()
    )
    visible = rows[:safe_limit]
    next_cursor = None
    if len(rows) > safe_limit:
        last = visible[-1]
        next_cursor = encode_operator_cursor(
            priority=last.priority,
            task_id=last.id,
        )
    return {
        "items": [serialize_operator_task(row) for row in visible],
        "next_cursor": next_cursor,
        "filters": {
            "status": status,
            "source_type": source_type,
            "task_type": task_type,
        },
    }


def _get_task(db: Session, *, tenant_id: int, task_id: int) -> OperatorTask:
    row = (
        db.query(OperatorTask)
        .filter(
            OperatorTask.id == task_id,
            OperatorTask.tenant_id == tenant_id,
        )
        .first()
    )
    if not row:
        raise OperatorQueueError(
            404,
            "operator_task_not_found",
            "operator task not found",
        )
    return row


def transition_operator_task(
    db: Session,
    *,
    tenant_id: int,
    task_id: int,
    action: str,
    actor_id: int | None = None,
    note: str | None = None,
) -> OperatorTask:
    """Transition only Tenant-owned projection administrative tasks."""

    if action not in {"assign", "resolve", "drop"}:
        raise OperatorQueueError(
            400,
            "unsupported_operator_task_action",
            "unsupported operator task action",
        )
    row = _get_task(db, tenant_id=tenant_id, task_id=task_id)
    _ensure_task_mutable(row)
    if _is_source_owned_projection(row):
        raise OperatorQueueError(
            409,
            "operator_task_projection_command_forbidden",
            "source-owned projection must be changed through its aggregate command",
        )

    old_task = _task_snapshot(row)
    now = utc_now()
    if action == "assign":
        row.status = "assigned"
        row.assignee_id = actor_id
    else:
        row.status = "resolved" if action == "resolve" else "dropped"
        row.resolved_at = now
    row.updated_at = now
    db.flush()
    _log_operator_audit(
        db,
        actor_id=actor_id,
        action=action,
        row=row,
        old_value={"task": old_task},
        new_value={"task": _task_snapshot(row)},
        note=note,
    )
    return row


def create_webchat_handoff_task(
    db: Session,
    *,
    conversation: WebchatConversation,
    reason_code: str,
    payload: dict[str, Any] | None = None,
) -> OperatorTask:
    """Project one already-created HandoffRequest into OperatorTask."""

    raw_request_id = (payload or {}).get("handoff_request_id")
    try:
        request_id = int(raw_request_id)
    except (TypeError, ValueError) as exc:
        raise OperatorQueueError(
            409,
            "handoff_projection_source_missing",
            "handoff request id is required for projection",
        ) from exc
    request_row = db.get(WebchatHandoffRequest, request_id)
    if request_row is None or request_row.conversation_id != conversation.id:
        raise OperatorQueueError(
            409,
            "handoff_projection_source_mismatch",
            "handoff projection source does not match conversation",
        )
    ticket = (
        db.get(Ticket, request_row.ticket_id)
        if request_row.ticket_id
        else None
    )
    row, _ = _project_handoff_request(
        db,
        request_row=request_row,
        conversation=conversation,
        ticket=ticket,
    )
    if reason_code and not row.reason_code:
        row.reason_code = reason_code[:160]
    return row


__all__ = [
    "HANDOFF_PROJECTION_SCHEMA",
    "HANDOFF_PROJECTION_SOURCE",
    "OperatorQueueError",
    "ProjectResult",
    "create_operator_task",
    "create_webchat_handoff_task",
    "decode_operator_cursor",
    "encode_operator_cursor",
    "list_operator_tasks",
    "project_operator_queue",
    "project_webchat_handoff_tasks",
    "resolve_operator_task_tenant_id",
    "sanitize_operator_payload",
    "serialize_operator_task",
    "transition_operator_task",
]
