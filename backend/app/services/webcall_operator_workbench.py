from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..api.ticket_perf import _timeline_items
from ..models import Customer, Team, Ticket, User
from ..services.permissions import (
    CAP_CUSTOMER_PROFILE_READ,
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
    CAP_RUNTIME_MANAGE,
    resolve_capabilities,
    ensure_ticket_visible,
)
from ..services.webcall_ai.demo_lab import get_demo_lab_status
from ..services.webchat_handoff_service import list_handoff_queue
from ..services.webchat_performance import admin_list_conversations_optimized
from ..services.webchat_service import admin_get_thread
from ..services.webchat_voice_service import list_admin_incoming_voice_sessions, list_admin_voice_sessions
from ..utils.time import utc_now

REQUIRED_WORKBENCH_CAPABILITIES = {
    CAP_TICKET_READ,
    CAP_CUSTOMER_PROFILE_READ,
    CAP_WEBCALL_VOICE_QUEUE_VIEW,
    CAP_WEBCALL_VOICE_READ,
}
OPERATOR_WORKBENCH_CAPABILITIES = {
    CAP_WEBCALL_VOICE_ACCEPT,
    CAP_WEBCHAT_HANDOFF_ACCEPT,
    CAP_WEBCHAT_CONVERSATION_MONITOR_AI,
    CAP_WEBCHAT_HANDOFF_FORCE_TAKEOVER,
}


def _value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _dt(value: Any) -> str | None:
    return value.isoformat() if value else None


def _normalized(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _visible_capabilities(current_user: User, db: Session) -> set[str]:
    capabilities = resolve_capabilities(current_user, db)
    missing = sorted(REQUIRED_WORKBENCH_CAPABILITIES.difference(capabilities))
    if missing:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "webcall_operator_workbench_requires_capability", "missing": missing},
        )
    if not OPERATOR_WORKBENCH_CAPABILITIES.intersection(capabilities):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="webcall_operator_workbench_requires_operator_capability",
        )
    return capabilities


def _safe_handoff_queue(db: Session, current_user: User, capabilities: set[str], *, view: str, limit: int) -> dict[str, Any]:
    if view == "ai_active":
        if CAP_WEBCHAT_CONVERSATION_MONITOR_AI not in capabilities:
            return {"items": [], "view": view, "permissions": _permission_payload(capabilities), "blocked_reason": "webchat_monitor_ai_requires_capability"}
    elif CAP_WEBCHAT_HANDOFF_ACCEPT not in capabilities:
        return {"items": [], "view": view, "permissions": _permission_payload(capabilities), "blocked_reason": "webchat_handoff_accept_requires_capability"}
    return list_handoff_queue(db, current_user, view=view, limit=limit)


def _permission_payload(capabilities: set[str]) -> dict[str, bool]:
    return {
        "can_read_ticket": CAP_TICKET_READ in capabilities,
        "can_read_customer_profile": CAP_CUSTOMER_PROFILE_READ in capabilities,
        "can_read_voice": CAP_WEBCALL_VOICE_READ in capabilities,
        "can_view_voice_queue": CAP_WEBCALL_VOICE_QUEUE_VIEW in capabilities,
        "can_accept_voice": CAP_WEBCALL_VOICE_ACCEPT in capabilities,
        "can_reject_voice": CAP_WEBCALL_VOICE_REJECT in capabilities,
        "can_end_voice": CAP_WEBCALL_VOICE_END in capabilities,
        "can_accept_handoff": CAP_WEBCHAT_HANDOFF_ACCEPT in capabilities,
        "can_decline_handoff": CAP_WEBCHAT_HANDOFF_DECLINE in capabilities,
        "can_force_takeover": CAP_WEBCHAT_HANDOFF_FORCE_TAKEOVER in capabilities,
        "can_release_handoff": CAP_WEBCHAT_HANDOFF_RELEASE in capabilities,
        "can_resume_ai": CAP_WEBCHAT_HANDOFF_RESUME_AI in capabilities,
        "can_monitor_ai": CAP_WEBCHAT_CONVERSATION_MONITOR_AI in capabilities,
        "can_open_demo": CAP_RUNTIME_MANAGE in capabilities,
    }


def _merge_row(rows: dict[int, dict[str, Any]], next_row: dict[str, Any]) -> None:
    ticket_id = int(next_row["ticket_id"])
    existing = rows.get(ticket_id)
    if existing is None:
        rows[ticket_id] = next_row
        return
    priority = min(int(existing["priority"]), int(next_row["priority"]))
    winner = existing if int(existing["priority"]) <= int(next_row["priority"]) else next_row
    rows[ticket_id] = {
        **next_row,
        **existing,
        "key": winner["key"],
        "source": winner["source"],
        "priority": priority,
        "voice_session_id": existing.get("voice_session_id") or next_row.get("voice_session_id"),
        "handoff_request_id": existing.get("handoff_request_id") or next_row.get("handoff_request_id"),
        "handoff_status": existing.get("handoff_status") or next_row.get("handoff_status"),
        "ai_status": existing.get("ai_status") or next_row.get("ai_status"),
        "status": existing.get("status") or next_row.get("status"),
    }


def _visitor_label(*, name: str | None = None, email: str | None = None, phone: str | None = None, fallback: str | None = None) -> str:
    return name or email or phone or fallback or "Anonymous visitor"


def _workbench_rows(voice_items: list[dict[str, Any]], handoff_items: list[dict[str, Any]], conversations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: dict[int, dict[str, Any]] = {}
    for item in voice_items:
        _merge_row(rows, {
            "key": f"voice-{item.get('voice_session_id')}",
            "ticket_id": item["ticket_id"],
            "ticket_no": item.get("ticket_no"),
            "title": item.get("ticket_title"),
            "visitor_label": item.get("visitor_label") or "Anonymous visitor",
            "origin": item.get("origin"),
            "page_url": item.get("page_url"),
            "voice_session_id": item.get("voice_session_id"),
            "handoff_request_id": None,
            "handoff_status": None,
            "ai_status": None,
            "status": item.get("status"),
            "source": "voice",
            "priority": 0,
        })
    for item in handoff_items:
        _merge_row(rows, {
            "key": f"handoff-{item.get('id') or item.get('ticket_id')}",
            "ticket_id": item["ticket_id"],
            "ticket_no": item.get("ticket_no"),
            "title": item.get("title"),
            "visitor_label": _visitor_label(name=item.get("visitor_name"), email=item.get("visitor_email"), phone=item.get("visitor_phone")),
            "origin": item.get("origin"),
            "page_url": None,
            "voice_session_id": None,
            "handoff_request_id": item.get("id"),
            "handoff_status": item.get("status"),
            "ai_status": item.get("ai_status"),
            "status": None,
            "source": "handoff",
            "priority": 1 if item.get("status") == "requested" else 2,
        })
    for item in conversations:
        _merge_row(rows, {
            "key": f"conversation-{item.get('conversation_id')}",
            "ticket_id": item["ticket_id"],
            "ticket_no": item.get("ticket_no"),
            "title": item.get("title"),
            "visitor_label": _visitor_label(name=item.get("visitor_name"), email=item.get("visitor_email"), phone=item.get("visitor_phone")),
            "origin": item.get("origin"),
            "page_url": item.get("page_url"),
            "voice_session_id": None,
            "handoff_request_id": item.get("current_handoff_request_id"),
            "handoff_status": item.get("handoff_status"),
            "ai_status": item.get("ai_status"),
            "status": item.get("status"),
            "source": "conversation",
            "priority": 3 if item.get("needs_human") or item.get("ai_pending") else 4,
        })
    return sorted(rows.values(), key=lambda row: (int(row["priority"]), int(row["ticket_id"])))


def _ticket_summary(db: Session, current_user: User, ticket_id: int) -> dict[str, Any]:
    result = (
        db.query(Ticket, Customer, User, Team)
        .outerjoin(Customer, Customer.id == Ticket.customer_id)
        .outerjoin(User, User.id == Ticket.assignee_id)
        .outerjoin(Team, Team.id == Ticket.team_id)
        .filter(Ticket.id == ticket_id)
        .first()
    )
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    ticket, customer, assignee, team = result
    ensure_ticket_visible(current_user, ticket, db)
    return {
        "id": ticket.id,
        "ticket_no": ticket.ticket_no,
        "title": ticket.title,
        "description": ticket.description,
        "issue_summary": ticket.issue_summary,
        "status": _value(ticket.status),
        "priority": _value(ticket.priority),
        "source": _value(ticket.source),
        "source_channel": _value(ticket.source_channel),
        "category": ticket.category,
        "sub_category": ticket.sub_category,
        "tracking_number": ticket.tracking_number,
        "case_type": ticket.case_type,
        "customer_request": ticket.customer_request,
        "last_customer_message": ticket.last_customer_message,
        "required_action": ticket.required_action,
        "missing_fields": ticket.missing_fields,
        "customer_update": ticket.customer_update,
        "resolution_summary": ticket.resolution_summary,
        "conversation_state": _value(ticket.conversation_state),
        "customer": {
            "id": customer.id,
            "name": customer.name,
            "email": customer.email,
            "phone": customer.phone,
            "external_ref": customer.external_ref,
        } if customer else None,
        "customer_name": customer.name if customer else None,
        "assignee": {
            "id": assignee.id,
            "username": assignee.username,
            "display_name": assignee.display_name,
            "role": _value(assignee.role),
        } if assignee else None,
        "assignee_name": assignee.display_name if assignee else None,
        "team": {"id": team.id, "name": team.name} if team else None,
        "team_name": team.name if team else None,
        "market_code": ticket.market.code if ticket.market else None,
        "country_code": ticket.country_code,
        "ai_summary": ticket.ai_summary,
        "ai_classification": ticket.ai_classification,
        "ai_confidence": ticket.ai_confidence,
        "preferred_reply_channel": ticket.preferred_reply_channel,
        "preferred_reply_contact": ticket.preferred_reply_contact,
        "created_at": _dt(ticket.created_at),
        "updated_at": _dt(ticket.updated_at),
        "first_response_due_at": _dt(ticket.first_response_due_at),
        "resolution_due_at": _dt(ticket.resolution_due_at),
    }


def _identity_verification(thread: dict[str, Any] | None, ticket: dict[str, Any] | None, row: dict[str, Any] | None) -> dict[str, Any]:
    visitor = (thread or {}).get("visitor") or {}
    customer = (ticket or {}).get("customer") or {}
    visitor_name = visitor.get("name") or (row or {}).get("visitor_label")
    visitor_email = visitor.get("email")
    visitor_phone = visitor.get("phone")
    ticket_name = (ticket or {}).get("customer_name") or customer.get("name")
    preferred_contact = (ticket or {}).get("preferred_reply_contact")
    ticket_email = customer.get("email") or (preferred_contact if "@" in str(preferred_contact or "") else None)
    ticket_phone = customer.get("phone") or (preferred_contact if preferred_contact and "@" not in str(preferred_contact) else None)
    checks = {
        "name": bool(visitor_name and ticket_name and _normalized(visitor_name) == _normalized(ticket_name)),
        "email": bool(visitor_email and ticket_email and _normalized(visitor_email) == _normalized(ticket_email)),
        "phone": bool(visitor_phone and ticket_phone and _normalized(visitor_phone) == _normalized(ticket_phone)),
    }
    basis = [key for key, value in checks.items() if value]
    return {
        "verification_status": "matched" if basis else "needs_review",
        "match_basis": basis,
        "visitor": {"name": visitor_name, "email": visitor_email, "phone": visitor_phone},
        "ticket_customer": {"name": ticket_name, "email": ticket_email, "phone": ticket_phone},
        "tracking_number": (ticket or {}).get("tracking_number"),
        "market_code": (ticket or {}).get("market_code"),
        "country_code": (ticket or {}).get("country_code"),
    }


def _ai_suggestions(thread: dict[str, Any] | None, ticket: dict[str, Any] | None, handoff: dict[str, Any] | None) -> list[dict[str, str]]:
    latest_ai_turn = ((thread or {}).get("ai_turns") or [])[-1] if (thread or {}).get("ai_turns") else None
    candidates = [
        ("Recommended action", (handoff or {}).get("recommended_agent_action") or (thread or {}).get("required_action") or (ticket or {}).get("required_action"), "handoff_or_ticket"),
        ("Missing information", (ticket or {}).get("missing_fields"), "ticket_summary"),
        ("Customer update", (ticket or {}).get("customer_update"), "ticket_summary"),
        ("AI summary", (ticket or {}).get("ai_summary"), "ticket_summary"),
    ]
    if latest_ai_turn:
        summary = " / ".join(
            str(value)
            for value in [
                latest_ai_turn.get("status"),
                latest_ai_turn.get("reply_source"),
                latest_ai_turn.get("fallback_reason"),
            ]
            if value
        )
        candidates.append(("Latest AI turn", summary, "webchat_ai_turn"))
    return [{"label": label, "text": str(text), "source": source} for label, text, source in candidates if text]


def _selected_payload(db: Session, current_user: User, ticket_id: int, row: dict[str, Any] | None) -> dict[str, Any]:
    ticket = _ticket_summary(db, current_user, ticket_id)
    try:
        thread = admin_get_thread(db, ticket_id, current_user)
    except HTTPException as exc:
        if exc.status_code != status.HTTP_404_NOT_FOUND:
            raise
        thread = None
    voice_sessions = list_admin_voice_sessions(db, ticket_id=ticket_id, current_user=current_user)
    timeline_items = _timeline_items(db, ticket_id, None, 20)
    handoff = (thread or {}).get("handoff") if thread else None
    identity = _identity_verification(thread, ticket, row)
    suggestions = _ai_suggestions(thread, ticket, handoff)
    return {
        "ticket_id": ticket_id,
        "row": row,
        "ticket": ticket,
        "thread": thread,
        "handoff": handoff,
        "voice_sessions": voice_sessions,
        "timeline": {"items": timeline_items[:20], "next_cursor": None, "has_more": len(timeline_items) > 20},
        "identity": identity,
        "ai_suggestions": suggestions,
    }


def _demo_payload(db: Session, current_user: User, capabilities: set[str]) -> dict[str, Any]:
    if CAP_RUNTIME_MANAGE not in capabilities:
        return {"visible": False, "status": None, "blocked_reason": "runtime_manage_required"}
    return {"visible": True, "status": get_demo_lab_status(db, current_user), "blocked_reason": None}


def build_webcall_operator_workbench(
    db: Session,
    current_user: User,
    *,
    view: str = "requested",
    voice_status: str = "incoming",
    ticket_id: int | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    capabilities = _visible_capabilities(current_user, db)
    safe_limit = max(1, min(int(limit or 50), 100))
    voice_queue = list_admin_incoming_voice_sessions(db, current_user=current_user, status_filter=voice_status, limit=safe_limit)
    handoff_queue = _safe_handoff_queue(db, current_user, capabilities, view=view, limit=safe_limit)
    conversations = admin_list_conversations_optimized(db, current_user, limit=safe_limit)
    rows = _workbench_rows(voice_queue.get("items", []), handoff_queue.get("items", []), conversations)
    selected_ticket_id = ticket_id or (int(rows[0]["ticket_id"]) if rows else None)
    selected_row = next((row for row in rows if selected_ticket_id and int(row["ticket_id"]) == int(selected_ticket_id)), None)
    selected = _selected_payload(db, current_user, int(selected_ticket_id), selected_row) if selected_ticket_id else None

    return {
        "generated_at": _dt(utc_now()),
        "filters": {"view": view, "voice_status": voice_status, "limit": safe_limit},
        "rows": rows,
        "selected_ticket_id": selected_ticket_id,
        "selected": selected,
        "voice_queue": voice_queue,
        "handoff_queue": handoff_queue,
        "demo": _demo_payload(db, current_user, capabilities),
        "permissions": _permission_payload(capabilities),
        "source_contracts": [
            "/api/webchat/admin/voice/sessions",
            "/api/webchat/admin/tickets/{ticket_id}/voice/sessions",
            "/api/webchat/admin/tickets/{ticket_id}/voice/{voice_session_id}/accept",
            "/api/webchat/admin/tickets/{ticket_id}/voice/{voice_session_id}/reject",
            "/api/webchat/admin/tickets/{ticket_id}/voice/{voice_session_id}/end",
            "/api/webchat/admin/handoff/queue",
            "/api/webchat/admin/handoff/{request_id}/accept",
            "/api/webchat/admin/handoff/{request_id}/decline",
            "/api/webchat/admin/handoff/{request_id}/release",
            "/api/webchat/admin/handoff/{request_id}/resume-ai",
            "/api/webchat/admin/tickets/{ticket_id}/force-takeover",
            "/api/webchat/admin/tickets/{ticket_id}/thread",
            "/api/tickets/{ticket_id}/timeline",
            "/api/admin/webcall-ai-demo/status",
        ],
    }
