from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..enums import ConversationState, EventType, TicketStatus, UserRole
from ..models import Ticket, User
from ..operator_models import OperatorTask
from ..utils.time import utc_now
from ..webchat_models import (
    WebchatConversation,
    WebchatHandoffDecision,
    WebchatHandoffRequest,
    WebchatMessage,
)
from .audit_service import log_admin_audit
from .operator_queue import create_webchat_handoff_task
from .permissions import (
    CAP_WEBCHAT_HANDOFF_ACCEPT,
    CAP_WEBCHAT_HANDOFF_DECLINE,
    CAP_WEBCHAT_HANDOFF_FORCE_TAKEOVER,
    CAP_WEBCHAT_HANDOFF_RELEASE,
    CAP_WEBCHAT_HANDOFF_RESUME_AI,
    ensure_ticket_visible,
    resolve_capabilities,
)
from .ticket_event_writer import TicketEventClass, TicketEventWriter
from .webchat_ai_turn_service import (
    ai_snapshot,
    cancel_open_ai_turns_for_handoff,
    safe_write_webchat_event,
)
from .webchat_inbox_read_state import webchat_read_state_payload

OPEN_HANDOFF_STATUSES = {"requested", "accepted"}
TERMINAL_HANDOFF_STATUSES = {"closed", "cancelled", "expired", "resumed_ai"}
AI_ACTIVE_STATUSES = {"queued", "processing", "bridge_calling", "fallback_generating"}
MAX_NOTE_CHARS = 1000
MAX_REASON_CHARS = 240
MAX_ACTION_CHARS = 1000


def _clip(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).strip().split())
    return cleaned[:limit] if cleaned else None


def _status_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _elapsed_seconds_since(start: Any) -> int:
    if not start:
        return 0
    now = utc_now()
    try:
        if getattr(start, "tzinfo", None) is None and getattr(now, "tzinfo", None) is not None:
            now = now.replace(tzinfo=None)
        elif getattr(start, "tzinfo", None) is not None and getattr(now, "tzinfo", None) is None:
            now = now.replace(tzinfo=start.tzinfo)
        return max(0, int((now - start).total_seconds()))
    except Exception:
        return 0


def _query_with_lock(db: Session, query):
    if db.bind and db.bind.dialect.name.startswith("postgresql"):
        return query.with_for_update()
    return query


def _active_request_query(db: Session, *, conversation_id: int):
    return db.query(WebchatHandoffRequest).filter(
        WebchatHandoffRequest.conversation_id == conversation_id,
        WebchatHandoffRequest.status.in_(OPEN_HANDOFF_STATUSES),
    )


def _active_request_for_conversation(db: Session, *, conversation_id: int, lock: bool = False) -> WebchatHandoffRequest | None:
    query = _active_request_query(db, conversation_id=conversation_id).order_by(WebchatHandoffRequest.id.desc())
    if lock:
        query = _query_with_lock(db, query)
    return query.first()


def _request_by_id(db: Session, request_id: int, *, lock: bool = False) -> WebchatHandoffRequest:
    query = db.query(WebchatHandoffRequest).filter(WebchatHandoffRequest.id == request_id)
    if lock:
        query = _query_with_lock(db, query)
    row = query.first()
    if row is None:
        raise HTTPException(status_code=404, detail="webchat handoff request not found")
    return row


def _load_conversation_ticket(db: Session, row: WebchatHandoffRequest) -> tuple[WebchatConversation, Ticket]:
    conversation = db.query(WebchatConversation).filter(WebchatConversation.id == row.conversation_id).first()
    ticket = db.query(Ticket).filter(Ticket.id == row.ticket_id).first()
    if conversation is None or ticket is None:
        raise HTTPException(status_code=409, detail="webchat handoff source is missing")
    return conversation, ticket


def _last_message(db: Session, conversation_id: int) -> WebchatMessage | None:
    return (
        db.query(WebchatMessage)
        .filter(WebchatMessage.conversation_id == conversation_id)
        .order_by(WebchatMessage.id.desc())
        .first()
    )


def _visible_from_preloaded(user: User, ticket: Ticket, capabilities: set[str]) -> bool:
    if user.role in {UserRole.admin, UserRole.manager, UserRole.auditor}:
        return True
    if ticket.assignee_id == user.id:
        return True
    if user.team_id and ticket.team_id == user.team_id:
        return True
    return CAP_WEBCHAT_HANDOFF_FORCE_TAKEOVER in capabilities


def _ensure_visible(user: User, ticket: Ticket, db: Session) -> None:
    ensure_ticket_visible(user, ticket, db)


def _write_ticket_event(
    db: Session,
    *,
    ticket_id: int,
    actor_id: int | None,
    event_type: EventType,
    note: str,
    payload: dict[str, Any],
) -> None:
    TicketEventWriter.add(
        db,
        ticket_id=ticket_id,
        actor_id=actor_id,
        event_type=event_type,
        event_class=TicketEventClass.INTERNAL_AUDIT,
        note=note,
        payload=payload,
    )


def _write_handoff_event(
    db: Session,
    *,
    conversation: WebchatConversation,
    ticket: Ticket,
    request_row: WebchatHandoffRequest,
    event_type: str,
    actor_id: int | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    base_payload = {
        "handoff_request_id": request_row.id,
        "status": request_row.status,
        "source": request_row.source,
        "trigger_type": request_row.trigger_type,
        "actor_id": actor_id,
    }
    base_payload.update(payload or {})
    safe_write_webchat_event(
        db,
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        event_type=event_type,
        payload=base_payload,
    )
    _write_ticket_event(
        db,
        ticket_id=ticket.id,
        actor_id=actor_id,
        event_type=EventType.conversation_state_changed,
        note=event_type.replace(".", " "),
        payload={"public_conversation_id": conversation.public_id, **base_payload},
    )


def _sync_conversation_snapshot(
    *,
    conversation: WebchatConversation,
    request_row: WebchatHandoffRequest | None,
    status_value: str,
    active_agent_id: int | None,
    ai_suspended: bool,
    ai_suspended_by: int | None,
    ai_suspended_reason: str | None,
    takeover_mode: str | None,
) -> None:
    now = utc_now()
    conversation.current_handoff_request_id = request_row.id if request_row is not None else None
    conversation.handoff_status = status_value
    conversation.active_agent_id = active_agent_id
    conversation.ai_suspended = ai_suspended
    if ai_suspended:
        conversation.ai_suspended_at = conversation.ai_suspended_at or now
        conversation.ai_suspended_by = ai_suspended_by
        conversation.ai_suspended_reason = _clip(ai_suspended_reason, MAX_REASON_CHARS)
    else:
        conversation.ai_suspended_at = None
        conversation.ai_suspended_by = None
        conversation.ai_suspended_reason = None
    conversation.takeover_mode = takeover_mode
    conversation.last_handoff_reason = _clip(request_row.reason_code or request_row.reason_text, MAX_REASON_CHARS) if request_row else None
    conversation.updated_at = now


def _sync_operator_task(
    db: Session,
    *,
    conversation: WebchatConversation,
    request_row: WebchatHandoffRequest,
    status_value: str,
    actor_id: int | None = None,
) -> None:
    task = (
        db.query(OperatorTask)
        .filter(
            OperatorTask.source_type == "webchat",
            OperatorTask.webchat_conversation_id == conversation.id,
            OperatorTask.task_type == "handoff",
            OperatorTask.status.notin_(["resolved", "dropped", "replayed", "replay_failed", "cancelled"]),
        )
        .order_by(OperatorTask.id.desc())
        .first()
    )
    if task is None:
        return
    task.status = status_value
    if status_value == "assigned":
        task.assignee_id = actor_id
    elif status_value == "pending":
        task.assignee_id = None
    task.updated_at = utc_now()
    payload = {}
    try:
        payload = json.loads(task.payload_json or "{}")
    except Exception:
        payload = {}
    if isinstance(payload, dict):
        payload["handoff_request_id"] = request_row.id
        payload["handoff_status"] = request_row.status
        task.payload_json = json.dumps(payload, ensure_ascii=False, default=str)


def _declined_by_user(db: Session, *, request_id: int, user_id: int) -> bool:
    return bool(
        db.query(WebchatHandoffDecision.id)
        .filter(
            WebchatHandoffDecision.request_id == request_id,
            WebchatHandoffDecision.actor_id == user_id,
            WebchatHandoffDecision.decision == "declined",
        )
        .order_by(WebchatHandoffDecision.id.desc())
        .first()
    )


def _serialize_last_message(row: WebchatMessage | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "direction": row.direction,
        "body_text": row.body_text or row.body,
        "message_type": row.message_type,
        "author_label": row.author_label,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def serialize_handoff_request(
    db: Session,
    request_row: WebchatHandoffRequest,
    *,
    current_user: User | None = None,
    conversation: WebchatConversation | None = None,
    ticket: Ticket | None = None,
) -> dict[str, Any]:
    if conversation is None:
        conversation = db.query(WebchatConversation).filter(WebchatConversation.id == request_row.conversation_id).first()
    if ticket is None:
        ticket = db.query(Ticket).filter(Ticket.id == request_row.ticket_id).first()
    last_message = _last_message(db, request_row.conversation_id)
    declined_by_me = bool(current_user and _declined_by_user(db, request_id=request_row.id, user_id=current_user.id))
    capabilities = resolve_capabilities(current_user, db) if current_user else set()
    waiting_seconds = _elapsed_seconds_since(request_row.requested_at)
    can_force_takeover = CAP_WEBCHAT_HANDOFF_FORCE_TAKEOVER in capabilities
    payload: dict[str, Any] = {
        "id": request_row.id,
        "conversation_id": conversation.public_id if conversation else None,
        "webchat_conversation_id": request_row.conversation_id,
        "ticket_id": request_row.ticket_id,
        "ticket_no": ticket.ticket_no if ticket else None,
        "title": ticket.title if ticket else None,
        "status": request_row.status,
        "source": request_row.source,
        "trigger_type": request_row.trigger_type,
        "reason_code": request_row.reason_code,
        "reason_text": request_row.reason_text,
        "recommended_agent_action": request_row.recommended_agent_action,
        "assigned_agent_id": request_row.assigned_agent_id,
        "accepted_by_user_id": request_row.accepted_by_user_id,
        "forced_by_user_id": request_row.forced_by_user_id,
        "declined_by_me": declined_by_me,
        "waiting_seconds": max(0, waiting_seconds),
        "requested_at": request_row.requested_at.isoformat() if request_row.requested_at else None,
        "accepted_at": request_row.accepted_at.isoformat() if request_row.accepted_at else None,
        "released_at": request_row.released_at.isoformat() if request_row.released_at else None,
        "closed_at": request_row.closed_at.isoformat() if request_row.closed_at else None,
        "handoff_status": conversation.handoff_status if conversation else request_row.status,
        "active_agent_id": conversation.active_agent_id if conversation else request_row.assigned_agent_id,
        "ai_suspended": bool(conversation.ai_suspended) if conversation else False,
        "ai_status": conversation.active_ai_status if conversation else None,
        "ai_turn_id": conversation.active_ai_turn_id if conversation else request_row.ai_turn_id,
        "takeover_mode": conversation.takeover_mode if conversation else None,
        "visitor_name": conversation.visitor_name if conversation else None,
        "visitor_email": conversation.visitor_email if conversation else None,
        "visitor_phone": conversation.visitor_phone if conversation else None,
        "origin": conversation.origin if conversation else None,
        "last_message": _serialize_last_message(last_message),
        "can_accept": bool(current_user and request_row.status == "requested" and CAP_WEBCHAT_HANDOFF_ACCEPT in capabilities),
        "can_decline": bool(current_user and request_row.status == "requested" and CAP_WEBCHAT_HANDOFF_DECLINE in capabilities),
        "can_force_takeover": can_force_takeover,
        "can_release": bool(
            current_user
            and request_row.status == "accepted"
            and CAP_WEBCHAT_HANDOFF_RELEASE in capabilities
            and (request_row.assigned_agent_id == current_user.id or can_force_takeover)
        ),
        "can_resume_ai": bool(current_user and request_row.status in OPEN_HANDOFF_STATUSES and CAP_WEBCHAT_HANDOFF_RESUME_AI in capabilities),
        "can_reply": bool(current_user and conversation and conversation.handoff_status == "accepted" and conversation.active_agent_id == current_user.id),
    }
    if conversation:
        payload.update(ai_snapshot(conversation))
        if current_user:
            payload.update(webchat_read_state_payload(db, conversation_id=conversation.id, user_id=current_user.id))
    return payload


def request_webchat_handoff(
    db: Session,
    *,
    conversation: WebchatConversation,
    ticket: Ticket,
    source: str,
    trigger_type: str,
    reason_code: str | None = None,
    reason_text: str | None = None,
    recommended_agent_action: str | None = None,
    trigger_message_id: int | None = None,
    ai_turn_id: int | None = None,
    requested_by_actor_type: str = "system",
    requested_by_user_id: int | None = None,
    note: str | None = None,
) -> WebchatHandoffRequest:
    existing = _active_request_for_conversation(db, conversation_id=conversation.id, lock=True)
    now = utc_now()
    reason = _clip(reason_code or reason_text or "human_review_required", MAX_REASON_CHARS) or "human_review_required"
    if existing is not None:
        existing.reason_code = existing.reason_code or _clip(reason_code, 160)
        existing.reason_text = existing.reason_text or _clip(reason_text, MAX_REASON_CHARS)
        existing.recommended_agent_action = existing.recommended_agent_action or _clip(recommended_agent_action, MAX_ACTION_CHARS)
        existing.trigger_message_id = existing.trigger_message_id or trigger_message_id
        existing.ai_turn_id = existing.ai_turn_id or ai_turn_id
        existing.updated_at = now
        request_row = existing
        created = False
    else:
        request_row = WebchatHandoffRequest(
            conversation_id=conversation.id,
            ticket_id=ticket.id,
            source=_clip(source, 40) or "ai_auto",
            trigger_type=_clip(trigger_type, 80) or "handoff_required",
            status="requested",
            reason_code=_clip(reason_code or reason, 160),
            reason_text=_clip(reason_text, MAX_REASON_CHARS),
            recommended_agent_action=_clip(recommended_agent_action, MAX_ACTION_CHARS),
            trigger_message_id=trigger_message_id,
            ai_turn_id=ai_turn_id,
            requested_by_actor_type=_clip(requested_by_actor_type, 40) or "system",
            requested_by_user_id=requested_by_user_id,
            requested_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(request_row)
        db.flush()
        created = True

    ticket.required_action = _clip(recommended_agent_action, MAX_ACTION_CHARS) or ticket.required_action or reason
    ticket.conversation_state = ConversationState.human_review_required
    if ticket.status in {TicketStatus.new, TicketStatus.resolved, TicketStatus.closed, TicketStatus.canceled}:
        ticket.status = TicketStatus.pending_assignment
    ticket.updated_at = now
    _sync_conversation_snapshot(
        conversation=conversation,
        request_row=request_row,
        status_value="requested",
        active_agent_id=None,
        ai_suspended=True,
        ai_suspended_by=requested_by_user_id,
        ai_suspended_reason=reason,
        takeover_mode=None,
    )
    cancelled_turns = cancel_open_ai_turns_for_handoff(
        db,
        conversation=conversation,
        actor_id=requested_by_user_id,
        reason_code="handoff_requested",
    )
    create_webchat_handoff_task(
        db,
        conversation=conversation,
        reason_code=request_row.reason_code or reason,
        payload={
            "handoff_request_id": request_row.id,
            "source": request_row.source,
            "trigger_type": request_row.trigger_type,
            "reason_code": request_row.reason_code,
            "recommended_agent_action": request_row.recommended_agent_action,
            "ticket_no": ticket.ticket_no,
            "conversation_state": _status_value(ticket.conversation_state),
            "visitor_name": conversation.visitor_name,
            "origin": conversation.origin,
        },
    )
    _write_handoff_event(
        db,
        conversation=conversation,
        ticket=ticket,
        request_row=request_row,
        event_type="handoff.requested" if created else "handoff.request_updated",
        actor_id=requested_by_user_id,
        payload={"reason": reason, "cancelled_ai_turns": cancelled_turns, "note": _clip(note, MAX_NOTE_CHARS)},
    )
    if created:
        log_admin_audit(
            db,
            actor_id=requested_by_user_id,
            action="webchat_handoff.requested",
            target_type="webchat_handoff_request",
            target_id=request_row.id,
            new_value={"conversation_id": conversation.id, "ticket_id": ticket.id, "source": request_row.source, "reason": reason},
        )
    db.flush()
    return request_row


def list_handoff_queue(
    db: Session,
    current_user: User,
    *,
    view: str = "requested",
    include_declined: bool = False,
    limit: int = 50,
) -> dict[str, Any]:
    capabilities = resolve_capabilities(current_user, db)
    queue_permissions = {
        "can_accept": CAP_WEBCHAT_HANDOFF_ACCEPT in capabilities,
        "can_decline": CAP_WEBCHAT_HANDOFF_DECLINE in capabilities,
        "can_force_takeover": CAP_WEBCHAT_HANDOFF_FORCE_TAKEOVER in capabilities,
        "can_release": CAP_WEBCHAT_HANDOFF_RELEASE in capabilities,
        "can_resume_ai": CAP_WEBCHAT_HANDOFF_RESUME_AI in capabilities,
    }
    safe_limit = max(1, min(int(limit or 50), 100))
    items: list[dict[str, Any]] = []

    if view == "ai_active":
        rows = (
            db.query(WebchatConversation, Ticket)
            .join(Ticket, Ticket.id == WebchatConversation.ticket_id)
            .filter(
                WebchatConversation.status == "open",
                WebchatConversation.ai_suspended.is_(False),
                WebchatConversation.active_ai_status.in_(AI_ACTIVE_STATUSES),
            )
            .order_by(WebchatConversation.active_ai_updated_at.desc(), WebchatConversation.updated_at.desc())
            .limit(safe_limit)
            .all()
        )
        for conversation, ticket in rows:
            if not _visible_from_preloaded(current_user, ticket, capabilities):
                continue
            items.append({
                "id": None,
                "conversation_id": conversation.public_id,
                "webchat_conversation_id": conversation.id,
                "ticket_id": ticket.id,
                "ticket_no": ticket.ticket_no,
                "title": ticket.title,
                "status": "ai_active",
                "source": "ai_active",
                "trigger_type": "monitor_ai",
                "reason_code": conversation.active_ai_status,
                "reason_text": "AI is currently handling this conversation",
                "recommended_agent_action": "Force takeover if the AI conversation needs human intervention.",
                "assigned_agent_id": conversation.active_agent_id,
                "declined_by_me": False,
                "waiting_seconds": 0,
                "requested_at": None,
                "handoff_status": conversation.handoff_status,
                "active_agent_id": conversation.active_agent_id,
                "ai_suspended": bool(conversation.ai_suspended),
                "ai_status": conversation.active_ai_status,
                "ai_turn_id": conversation.active_ai_turn_id,
                "takeover_mode": conversation.takeover_mode,
                "visitor_name": conversation.visitor_name,
                "visitor_email": conversation.visitor_email,
                "visitor_phone": conversation.visitor_phone,
                "origin": conversation.origin,
                "last_message": _serialize_last_message(_last_message(db, conversation.id)),
                "can_accept": False,
                "can_decline": False,
                "can_force_takeover": CAP_WEBCHAT_HANDOFF_FORCE_TAKEOVER in capabilities,
                "can_release": False,
                "can_resume_ai": False,
                "can_reply": False,
                **webchat_read_state_payload(db, conversation_id=conversation.id, user_id=current_user.id),
                **ai_snapshot(conversation),
            })
        return {"items": items, "view": view, "permissions": queue_permissions}

    query = db.query(WebchatHandoffRequest, WebchatConversation, Ticket).join(
        WebchatConversation, WebchatConversation.id == WebchatHandoffRequest.conversation_id
    ).join(Ticket, Ticket.id == WebchatHandoffRequest.ticket_id)
    if view == "mine":
        query = query.filter(WebchatHandoffRequest.status == "accepted", WebchatHandoffRequest.assigned_agent_id == current_user.id)
    elif view == "closed":
        query = query.filter(WebchatHandoffRequest.status.in_(TERMINAL_HANDOFF_STATUSES))
    else:
        query = query.filter(WebchatHandoffRequest.status == "requested")
    rows = query.order_by(WebchatHandoffRequest.requested_at.asc(), WebchatHandoffRequest.id.asc()).limit(safe_limit * 2).all()
    for request_row, conversation, ticket in rows:
        if len(items) >= safe_limit:
            break
        if not _visible_from_preloaded(current_user, ticket, capabilities):
            continue
        if view == "requested" and not include_declined and _declined_by_user(db, request_id=request_row.id, user_id=current_user.id):
            continue
        items.append(serialize_handoff_request(db, request_row, current_user=current_user, conversation=conversation, ticket=ticket))
    return {"items": items, "view": view, "permissions": queue_permissions}


def accept_handoff_request(db: Session, *, request_id: int, current_user: User, note: str | None = None) -> dict[str, Any]:
    row = _request_by_id(db, request_id, lock=True)
    conversation, ticket = _load_conversation_ticket(db, row)
    _ensure_visible(current_user, ticket, db)
    if row.status == "accepted" and row.assigned_agent_id and row.assigned_agent_id != current_user.id:
        raise HTTPException(status_code=409, detail="webchat handoff already accepted by another agent")
    if row.status not in {"requested", "accepted"}:
        raise HTTPException(status_code=409, detail="webchat handoff request is not open")
    now = utc_now()
    old_value = {"status": row.status, "assigned_agent_id": row.assigned_agent_id}
    row.status = "accepted"
    row.accepted_by_user_id = current_user.id
    row.assigned_agent_id = current_user.id
    row.accepted_at = row.accepted_at or now
    row.decision_note = _clip(note, MAX_NOTE_CHARS)
    row.lock_version += 1
    row.updated_at = now
    ticket.assignee_id = current_user.id
    ticket.status = TicketStatus.in_progress
    ticket.conversation_state = ConversationState.human_owned
    ticket.required_action = None
    ticket.updated_at = now
    _sync_conversation_snapshot(
        conversation=conversation,
        request_row=row,
        status_value="accepted",
        active_agent_id=current_user.id,
        ai_suspended=True,
        ai_suspended_by=current_user.id,
        ai_suspended_reason="handoff_accepted",
        takeover_mode="forced" if row.source == "operator_forced" else "accepted",
    )
    cancelled_turns = cancel_open_ai_turns_for_handoff(db, conversation=conversation, actor_id=current_user.id, reason_code="handoff_accepted")
    _sync_operator_task(db, conversation=conversation, request_row=row, status_value="assigned", actor_id=current_user.id)
    _write_handoff_event(
        db,
        conversation=conversation,
        ticket=ticket,
        request_row=row,
        event_type="handoff.accepted",
        actor_id=current_user.id,
        payload={"cancelled_ai_turns": cancelled_turns, "note": _clip(note, MAX_NOTE_CHARS)},
    )
    log_admin_audit(
        db,
        actor_id=current_user.id,
        action="webchat_handoff.accepted",
        target_type="webchat_handoff_request",
        target_id=row.id,
        old_value=old_value,
        new_value={"status": row.status, "assigned_agent_id": row.assigned_agent_id},
    )
    db.flush()
    return serialize_handoff_request(db, row, current_user=current_user, conversation=conversation, ticket=ticket)


def decline_handoff_request(
    db: Session,
    *,
    request_id: int,
    current_user: User,
    reason_code: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    row = _request_by_id(db, request_id, lock=True)
    conversation, ticket = _load_conversation_ticket(db, row)
    _ensure_visible(current_user, ticket, db)
    if row.status != "requested":
        raise HTTPException(status_code=409, detail="only requested handoffs can be declined")
    decision = WebchatHandoffDecision(
        request_id=row.id,
        actor_id=current_user.id,
        decision="declined",
        reason_code=_clip(reason_code, 160) or "agent_skipped",
        note=_clip(note, MAX_NOTE_CHARS),
        created_at=utc_now(),
    )
    db.add(decision)
    row.decision_note = _clip(note, MAX_NOTE_CHARS)
    row.updated_at = utc_now()
    _write_handoff_event(
        db,
        conversation=conversation,
        ticket=ticket,
        request_row=row,
        event_type="handoff.declined",
        actor_id=current_user.id,
        payload={"reason_code": decision.reason_code, "note": decision.note},
    )
    log_admin_audit(
        db,
        actor_id=current_user.id,
        action="webchat_handoff.declined",
        target_type="webchat_handoff_request",
        target_id=row.id,
        new_value={"reason_code": decision.reason_code, "note": decision.note},
    )
    db.flush()
    return serialize_handoff_request(db, row, current_user=current_user, conversation=conversation, ticket=ticket)


def force_takeover_ticket(db: Session, *, ticket_id: int, current_user: User, reason_code: str | None = None, note: str | None = None) -> dict[str, Any]:
    if CAP_WEBCHAT_HANDOFF_FORCE_TAKEOVER not in resolve_capabilities(current_user, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="webchat_handoff_force_takeover_requires_capability")
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if ticket is None:
        raise HTTPException(status_code=404, detail="ticket not found")
    _ensure_visible(current_user, ticket, db)
    conversation_query = db.query(WebchatConversation).filter(WebchatConversation.ticket_id == ticket.id)
    conversation_query = _query_with_lock(db, conversation_query)
    conversation = conversation_query.first()
    if conversation is None:
        raise HTTPException(status_code=404, detail="webchat conversation not found for ticket")
    row = _active_request_for_conversation(db, conversation_id=conversation.id, lock=True)
    now = utc_now()
    if row is None:
        row = WebchatHandoffRequest(
            conversation_id=conversation.id,
            ticket_id=ticket.id,
            source="operator_forced",
            trigger_type="force_takeover",
            status="requested",
            reason_code=_clip(reason_code, 160) or "operator_forced_takeover",
            reason_text=_clip(note, MAX_REASON_CHARS),
            recommended_agent_action="Human agent forced takeover while AI was active.",
            ai_turn_id=conversation.active_ai_turn_id,
            requested_by_actor_type="agent",
            requested_by_user_id=current_user.id,
            forced_by_user_id=current_user.id,
            requested_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.flush()
    else:
        row.source = "operator_forced"
        row.trigger_type = "force_takeover"
        row.reason_code = _clip(reason_code, 160) or row.reason_code or "operator_forced_takeover"
        row.reason_text = _clip(note, MAX_REASON_CHARS) or row.reason_text
        row.forced_by_user_id = current_user.id
        row.ai_turn_id = row.ai_turn_id or conversation.active_ai_turn_id
        row.updated_at = now
    accept_handoff_request(db, request_id=row.id, current_user=current_user, note=note)
    row.forced_by_user_id = current_user.id
    conversation.takeover_mode = "forced"
    _write_handoff_event(
        db,
        conversation=conversation,
        ticket=ticket,
        request_row=row,
        event_type="handoff.force_takeover",
        actor_id=current_user.id,
        payload={"reason_code": row.reason_code, "note": _clip(note, MAX_NOTE_CHARS)},
    )
    db.flush()
    return serialize_handoff_request(db, row, current_user=current_user, conversation=conversation, ticket=ticket)


def release_handoff_request(db: Session, *, request_id: int, current_user: User, note: str | None = None) -> dict[str, Any]:
    row = _request_by_id(db, request_id, lock=True)
    conversation, ticket = _load_conversation_ticket(db, row)
    _ensure_visible(current_user, ticket, db)
    if row.status != "accepted":
        raise HTTPException(status_code=409, detail="only accepted handoffs can be released")
    if row.assigned_agent_id != current_user.id and CAP_WEBCHAT_HANDOFF_FORCE_TAKEOVER not in resolve_capabilities(current_user, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="webchat handoff is owned by another agent")
    now = utc_now()
    old_value = {"status": row.status, "assigned_agent_id": row.assigned_agent_id}
    row.status = "requested"
    row.assigned_agent_id = None
    row.accepted_by_user_id = None
    row.released_at = now
    row.decision_note = _clip(note, MAX_NOTE_CHARS)
    row.lock_version += 1
    row.updated_at = now
    if ticket.assignee_id == current_user.id:
        ticket.assignee_id = None
    ticket.status = TicketStatus.pending_assignment
    ticket.conversation_state = ConversationState.human_review_required
    ticket.required_action = row.recommended_agent_action or row.reason_code or "WebChat handoff waiting for human support"
    ticket.updated_at = now
    _sync_conversation_snapshot(
        conversation=conversation,
        request_row=row,
        status_value="requested",
        active_agent_id=None,
        ai_suspended=True,
        ai_suspended_by=current_user.id,
        ai_suspended_reason="handoff_released",
        takeover_mode=None,
    )
    _sync_operator_task(db, conversation=conversation, request_row=row, status_value="pending", actor_id=None)
    _write_handoff_event(
        db,
        conversation=conversation,
        ticket=ticket,
        request_row=row,
        event_type="handoff.released",
        actor_id=current_user.id,
        payload={"note": _clip(note, MAX_NOTE_CHARS)},
    )
    log_admin_audit(
        db,
        actor_id=current_user.id,
        action="webchat_handoff.released",
        target_type="webchat_handoff_request",
        target_id=row.id,
        old_value=old_value,
        new_value={"status": row.status, "assigned_agent_id": row.assigned_agent_id},
    )
    db.flush()
    return serialize_handoff_request(db, row, current_user=current_user, conversation=conversation, ticket=ticket)


def resume_ai_for_handoff(db: Session, *, request_id: int, current_user: User, note: str | None = None) -> dict[str, Any]:
    row = _request_by_id(db, request_id, lock=True)
    conversation, ticket = _load_conversation_ticket(db, row)
    _ensure_visible(current_user, ticket, db)
    if row.status not in OPEN_HANDOFF_STATUSES:
        raise HTTPException(status_code=409, detail="webchat handoff request is already terminal")
    now = utc_now()
    old_value = {"status": row.status, "assigned_agent_id": row.assigned_agent_id}
    row.status = "resumed_ai"
    row.assigned_agent_id = None
    row.closed_at = now
    row.decision_note = _clip(note, MAX_NOTE_CHARS)
    row.lock_version += 1
    row.updated_at = now
    ticket.required_action = None
    ticket.conversation_state = ConversationState.ai_active
    ticket.updated_at = now
    if ticket.assignee_id == current_user.id:
        ticket.assignee_id = None
    _sync_conversation_snapshot(
        conversation=conversation,
        request_row=None,
        status_value="none",
        active_agent_id=None,
        ai_suspended=False,
        ai_suspended_by=None,
        ai_suspended_reason=None,
        takeover_mode=None,
    )
    _sync_operator_task(db, conversation=conversation, request_row=row, status_value="resolved", actor_id=current_user.id)
    _write_handoff_event(
        db,
        conversation=conversation,
        ticket=ticket,
        request_row=row,
        event_type="ai.resumed",
        actor_id=current_user.id,
        payload={"note": _clip(note, MAX_NOTE_CHARS)},
    )
    log_admin_audit(
        db,
        actor_id=current_user.id,
        action="webchat_handoff.resume_ai",
        target_type="webchat_handoff_request",
        target_id=row.id,
        old_value=old_value,
        new_value={"status": row.status, "assigned_agent_id": row.assigned_agent_id},
    )
    db.flush()
    return serialize_handoff_request(db, row, current_user=current_user, conversation=conversation, ticket=ticket)


def ensure_can_reply_in_handoff(db: Session, *, conversation: WebchatConversation, ticket: Ticket, current_user: User) -> None:
    request_id = getattr(conversation, "current_handoff_request_id", None)
    if not request_id and (getattr(conversation, "handoff_status", None) in {None, "none"}):
        if getattr(conversation, "active_ai_status", None) in AI_ACTIVE_STATUSES and not getattr(conversation, "ai_suspended", False):
            raise HTTPException(status_code=409, detail="webchat ai is active; force takeover before replying")
        return
    if conversation.handoff_status == "accepted" and conversation.active_agent_id == current_user.id:
        return
    if conversation.handoff_status == "accepted":
        raise HTTPException(status_code=409, detail="webchat handoff is owned by another agent")
    raise HTTPException(status_code=409, detail="webchat handoff must be accepted before replying")
