from __future__ import annotations

import json
from typing import Any, Callable

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..models import Customer, Ticket, TicketEvent, User
from ..webchat_models import WebchatConversation, WebchatEvent, WebchatMessage
from .permissions import (
    CAP_RUNTIME_MANAGE,
    CAP_TICKET_READ,
    CAP_WEBCALL_VOICE_ACCEPT,
    CAP_WEBCALL_VOICE_END,
    CAP_WEBCALL_VOICE_QUEUE_VIEW,
    CAP_WEBCALL_VOICE_READ,
    CAP_WEBCALL_VOICE_REJECT,
    CAP_WEBCHAT_CONVERSATION_MONITOR_AI,
    CAP_WEBCHAT_HANDOFF_ACCEPT,
    CAP_WEBCHAT_HANDOFF_DECLINE,
    CAP_WEBCHAT_HANDOFF_FORCE_TAKEOVER,
    CAP_WEBCHAT_HANDOFF_RELEASE,
    CAP_WEBCHAT_HANDOFF_RESUME_AI,
    ensure_can_view_webcall_voice_queue,
    ensure_ticket_visible,
    resolve_capabilities,
)
from .webcall_ai.demo_lab import get_demo_lab_status
from .webchat_handoff_service import list_handoff_queue
from .webchat_performance import admin_list_conversations_optimized
from .webchat_service import admin_get_thread
from .webchat_voice_service import (
    ACCEPT_READY_STATUSES,
    REJECT_READY_STATUSES,
    TERMINAL_STATUSES,
    list_admin_incoming_voice_sessions,
    list_admin_voice_sessions,
)

CONTRACTS = {
    "operator_workbench": "/api/admin/webcall-ai/operator-workbench",
    "voice_queue": "/api/webchat/admin/voice/sessions",
    "voice_sessions": "/api/webchat/admin/tickets/{ticket_id}/voice/sessions",
    "voice_accept": "/api/webchat/admin/tickets/{ticket_id}/voice/{voice_session_id}/accept",
    "voice_reject": "/api/webchat/admin/tickets/{ticket_id}/voice/{voice_session_id}/reject",
    "voice_end": "/api/webchat/admin/tickets/{ticket_id}/voice/{voice_session_id}/end",
    "handoff_queue": "/api/webchat/admin/handoff/queue",
    "handoff_accept": "/api/webchat/admin/handoff/{request_id}/accept",
    "handoff_decline": "/api/webchat/admin/handoff/{request_id}/decline",
    "handoff_release": "/api/webchat/admin/handoff/{request_id}/release",
    "handoff_resume_ai": "/api/webchat/admin/handoff/{request_id}/resume-ai",
    "handoff_force_takeover": "/api/webchat/admin/tickets/{ticket_id}/force-takeover",
    "thread": "/api/webchat/admin/tickets/{ticket_id}/thread",
    "demo_status": "/api/admin/webcall-ai-demo/status",
}


def _value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _dt(value: Any) -> str | None:
    return value.isoformat() if value else None


def _loads(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except Exception:
        return {"raw": value}
    return parsed if isinstance(parsed, dict) else {"raw": parsed}


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).strip().split())
    return text or None


def _same(left: Any, right: Any) -> bool:
    left_text = _clean(left)
    right_text = _clean(right)
    return bool(left_text and right_text and left_text.casefold() == right_text.casefold())


def _safe_resource(loader: Callable[[], Any], fallback: Any, *, detail: str) -> Any:
    try:
        return loader()
    except HTTPException as exc:
        if exc.status_code != status.HTTP_403_FORBIDDEN:
            raise
        if isinstance(fallback, dict):
            payload = dict(fallback)
            payload.update({"unavailable": True, "detail": exc.detail or detail})
            return payload
        return fallback


def _capability_payload(capabilities: set[str]) -> dict[str, bool]:
    return {
        "ticket_read": CAP_TICKET_READ in capabilities,
        "voice_queue_view": CAP_WEBCALL_VOICE_QUEUE_VIEW in capabilities,
        "voice_read": CAP_WEBCALL_VOICE_READ in capabilities,
        "voice_accept": CAP_WEBCALL_VOICE_ACCEPT in capabilities,
        "voice_reject": CAP_WEBCALL_VOICE_REJECT in capabilities,
        "voice_end": CAP_WEBCALL_VOICE_END in capabilities,
        "handoff_accept": CAP_WEBCHAT_HANDOFF_ACCEPT in capabilities,
        "handoff_decline": CAP_WEBCHAT_HANDOFF_DECLINE in capabilities,
        "handoff_force_takeover": CAP_WEBCHAT_HANDOFF_FORCE_TAKEOVER in capabilities,
        "handoff_release": CAP_WEBCHAT_HANDOFF_RELEASE in capabilities,
        "handoff_resume_ai": CAP_WEBCHAT_HANDOFF_RESUME_AI in capabilities,
        "monitor_ai": CAP_WEBCHAT_CONVERSATION_MONITOR_AI in capabilities,
        "demo_manage": CAP_RUNTIME_MANAGE in capabilities,
    }


def _ticket_payload(ticket: Ticket) -> dict[str, Any]:
    customer = ticket.customer
    return {
        "id": ticket.id,
        "ticket_no": ticket.ticket_no,
        "title": ticket.title,
        "status": _value(ticket.status),
        "priority": _value(ticket.priority),
        "source_channel": _value(ticket.source_channel),
        "conversation_state": _value(ticket.conversation_state),
        "tracking_number": ticket.tracking_number,
        "required_action": ticket.required_action,
        "missing_fields": ticket.missing_fields,
        "customer_update": ticket.customer_update,
        "ai_summary": ticket.ai_summary,
        "preferred_reply_channel": ticket.preferred_reply_channel,
        "preferred_reply_contact": ticket.preferred_reply_contact,
        "market_code": ticket.market.code if ticket.market else None,
        "country_code": ticket.country_code,
        "customer": _customer_payload(customer),
    }


def _customer_payload(customer: Customer | None) -> dict[str, Any] | None:
    if customer is None:
        return None
    return {
        "id": customer.id,
        "name": customer.name,
        "email": customer.email,
        "phone": customer.phone,
        "external_ref": customer.external_ref,
    }


def _identity_payload(ticket: Ticket, conversation: WebchatConversation | None) -> dict[str, Any]:
    customer = ticket.customer
    visitor = {
        "name": conversation.visitor_name if conversation else None,
        "email": conversation.visitor_email if conversation else None,
        "phone": conversation.visitor_phone if conversation else None,
        "ref": conversation.visitor_ref if conversation else None,
    }
    ticket_customer = _customer_payload(customer) or {"name": None, "email": None, "phone": None, "external_ref": None}
    preferred_contact = ticket.preferred_reply_contact
    matches: list[str] = []
    if _same(visitor.get("name"), ticket_customer.get("name")):
        matches.append("name")
    if _same(visitor.get("email"), ticket_customer.get("email")):
        matches.append("email")
    if _same(visitor.get("phone"), ticket_customer.get("phone")):
        matches.append("phone")
    if _same(visitor.get("email"), preferred_contact):
        matches.append("preferred_reply_email")
    if _same(visitor.get("phone"), preferred_contact):
        matches.append("preferred_reply_phone")
    missing = [key for key, value in visitor.items() if key in {"name", "email", "phone"} and not value]
    verified = bool(matches)
    return {
        "verified": verified,
        "status": "verified" if verified else "manual_review_required",
        "matches": matches,
        "missing_visitor_fields": missing,
        "visitor": visitor,
        "ticket_customer": ticket_customer,
        "preferred_reply_contact": preferred_contact,
    }


def _ai_suggestions(ticket: Ticket, conversation: WebchatConversation | None, handoff: dict[str, Any] | None) -> list[dict[str, Any]]:
    candidates = [
        ("handoff_recommendation", "Recommended action", handoff.get("recommended_agent_action") if handoff else None),
        ("ticket_required_action", "Required action", ticket.required_action),
        ("missing_fields", "Missing information", ticket.missing_fields),
        ("customer_update", "Customer update", ticket.customer_update),
        ("ai_summary", "AI summary", ticket.ai_summary),
        ("active_ai_status", "Active AI status", getattr(conversation, "active_ai_status", None) if conversation else None),
        ("last_handoff_reason", "Last handoff reason", getattr(conversation, "last_handoff_reason", None) if conversation else None),
    ]
    items: list[dict[str, Any]] = []
    for source, label, text in candidates:
        cleaned = _clean(text)
        if cleaned:
            items.append({"source": source, "label": label, "text": cleaned[:1000]})
    return items


def _session_actions(voice_sessions: list[dict[str, Any]], capabilities: set[str], current_user: User) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for session in voice_sessions:
        status_value = str(session.get("status") or "")
        terminal = status_value in TERMINAL_STATUSES
        accepted_by = session.get("accepted_by_user_id")
        can_accept = (
            CAP_WEBCALL_VOICE_ACCEPT in capabilities
            and status_value in ACCEPT_READY_STATUSES
            and accepted_by in {None, current_user.id}
        )
        can_reject = (
            CAP_WEBCALL_VOICE_REJECT in capabilities
            and status_value in REJECT_READY_STATUSES
            and accepted_by is None
        )
        can_end = CAP_WEBCALL_VOICE_END in capabilities and not terminal
        items.append(
            {
                "voice_session_id": session.get("voice_session_id"),
                "status": status_value,
                "provider": session.get("provider"),
                "can_accept": can_accept,
                "can_reject": can_reject,
                "can_end": can_end,
                "accepted_by_user_id": accepted_by,
                "ended_by_user_id": session.get("ended_by_user_id"),
            }
        )
    primary = next((item["voice_session_id"] for item in items if item["can_accept"] or item["can_end"]), None)
    return {"primary_voice_session_id": primary, "items": items}


def _public_voice_sessions(resource: dict[str, Any]) -> dict[str, Any]:
    payload = dict(resource)
    payload["items"] = [
        {key: value for key, value in item.items() if key not in {"participant_token", "participant_identity"}}
        for item in payload.get("items", [])
    ]
    return payload


def _timeline_audit(db: Session, ticket_id: int) -> dict[str, Any]:
    ticket_events = (
        db.query(TicketEvent)
        .filter(TicketEvent.ticket_id == ticket_id)
        .order_by(TicketEvent.created_at.desc(), TicketEvent.id.desc())
        .limit(8)
        .all()
    )
    webchat_events = (
        db.query(WebchatEvent)
        .filter(WebchatEvent.ticket_id == ticket_id)
        .order_by(WebchatEvent.created_at.desc(), WebchatEvent.id.desc())
        .limit(8)
        .all()
    )
    voice_messages = (
        db.query(WebchatMessage)
        .filter(WebchatMessage.ticket_id == ticket_id, WebchatMessage.message_type == "voice_call")
        .order_by(WebchatMessage.created_at.desc(), WebchatMessage.id.desc())
        .limit(5)
        .all()
    )
    return {
        "writeback_sources": {
            "ticket_event_count": len(ticket_events),
            "webchat_event_count": len(webchat_events),
            "voice_call_message_count": len(voice_messages),
        },
        "ticket_events": [
            {
                "id": row.id,
                "event_type": _value(row.event_type),
                "note": row.note,
                "created_at": _dt(row.created_at),
                "payload": _loads(row.payload_json),
            }
            for row in ticket_events
        ],
        "webchat_events": [
            {
                "id": row.id,
                "event_type": row.event_type,
                "created_at": _dt(row.created_at),
                "payload": _loads(row.payload_json),
            }
            for row in webchat_events
        ],
        "voice_call_messages": [
            {
                "id": row.id,
                "body": row.body_text or row.body,
                "created_at": _dt(row.created_at),
                "payload": _loads(row.payload_json),
            }
            for row in voice_messages
        ],
    }


def _selected_context(db: Session, current_user: User, ticket_id: int, capabilities: set[str]) -> dict[str, Any]:
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ticket not found")
    ensure_ticket_visible(current_user, ticket, db)
    conversation = db.query(WebchatConversation).filter(WebchatConversation.ticket_id == ticket.id).first()
    thread = admin_get_thread(db, ticket.id, current_user) if conversation else None
    handoff = thread.get("handoff") if isinstance(thread, dict) else None
    voice_sessions_resource = _safe_resource(
        lambda: list_admin_voice_sessions(db, ticket_id=ticket.id, current_user=current_user),
        {"items": []},
        detail="webcall_voice_read_unavailable",
    )
    voice_sessions_resource = _public_voice_sessions(voice_sessions_resource)
    voice_sessions = list(voice_sessions_resource.get("items", []))
    return {
        "ticket_id": ticket.id,
        "ticket": _ticket_payload(ticket),
        "thread": thread,
        "handoff": handoff,
        "identity": _identity_payload(ticket, conversation),
        "ai_suggestions": _ai_suggestions(ticket, conversation, handoff),
        "voice_sessions": voice_sessions_resource,
        "session_actions": _session_actions(voice_sessions, capabilities, current_user),
        "timeline_audit": _timeline_audit(db, ticket.id),
    }


def build_operator_workbench(
    db: Session,
    current_user: User,
    *,
    ticket_id: int | None = None,
    handoff_view: str = "requested",
    voice_status: str = "incoming",
    limit: int = 50,
) -> dict[str, Any]:
    ensure_can_view_webcall_voice_queue(current_user, db)
    capabilities = resolve_capabilities(current_user, db)
    safe_limit = max(1, min(int(limit or 50), 100))
    voice_queue = list_admin_incoming_voice_sessions(
        db,
        current_user=current_user,
        status_filter=voice_status,
        limit=safe_limit,
    )
    handoff_queue = _safe_resource(
        lambda: list_handoff_queue(db, current_user, view=handoff_view, limit=safe_limit),
        {"items": [], "view": handoff_view, "permissions": {}},
        detail="webchat_handoff_unavailable",
    )
    conversation_queue = _safe_resource(
        lambda: {"items": admin_list_conversations_optimized(db, current_user, limit=safe_limit)},
        {"items": []},
        detail="webchat_conversations_unavailable",
    )
    demo = {
        "available": CAP_RUNTIME_MANAGE in capabilities,
        "requires_capability": CAP_RUNTIME_MANAGE,
        "status": get_demo_lab_status(db, current_user) if CAP_RUNTIME_MANAGE in capabilities else None,
    }
    return {
        "ok": True,
        "route": {"operator": "/webcall/operator", "legacy": "/webcall", "customer_room": "/webcall/{voice_session_id}"},
        "contracts": CONTRACTS,
        "capabilities": _capability_payload(capabilities),
        "voice_queue": voice_queue,
        "handoff_queue": handoff_queue,
        "conversation_queue": conversation_queue,
        "demo": demo,
        "selected": _selected_context(db, current_user, ticket_id, capabilities) if ticket_id else None,
    }
