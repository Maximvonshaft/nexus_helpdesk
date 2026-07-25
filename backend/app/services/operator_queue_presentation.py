"""Server-owned display projection for the canonical operator queue.

This module enriches already-authorized queue items with human-readable business
identity. It does not create another queue or infer lifecycle truth; queue_id and
case_key remain the technical linkage while display fields are presentation-only.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..models import Ticket
from ..webchat_models import WebchatConversation


def _bounded_text(value: object, limit: int) -> str | None:
    text = str(value or "").strip()
    return text[:limit] if text else None


def project_unified_queue_display(db: Session, result: dict[str, Any]) -> dict[str, Any]:
    items = result.get("items")
    if not isinstance(items, list) or not items:
        return result

    ticket_ids = sorted(
        {
            int(item["ticket_id"])
            for item in items
            if isinstance(item, dict) and isinstance(item.get("ticket_id"), int)
        }
    )
    conversation_ids = sorted(
        {
            int(item["conversation_id"])
            for item in items
            if isinstance(item, dict)
            and item.get("ticket_id") is None
            and isinstance(item.get("conversation_id"), int)
        }
    )

    tickets = {
        row.id: row
        for row in (
            db.query(Ticket.id, Ticket.ticket_no, Ticket.title)
            .filter(Ticket.id.in_(ticket_ids))
            .all()
            if ticket_ids
            else []
        )
    }
    conversations = {
        row.id: row
        for row in (
            db.query(
                WebchatConversation.id,
                WebchatConversation.visitor_name,
                WebchatConversation.last_intent,
            )
            .filter(WebchatConversation.id.in_(conversation_ids))
            .all()
            if conversation_ids
            else []
        )
    }

    for item in items:
        if not isinstance(item, dict):
            continue
        ticket = tickets.get(item.get("ticket_id"))
        conversation = conversations.get(item.get("conversation_id"))
        source_type = str(item.get("source_type") or "")

        if ticket is not None:
            item["display_label"] = _bounded_text(ticket.ticket_no, 160) or "客服工单"
            item["display_summary"] = _bounded_text(ticket.title, 255)
        elif source_type == "handoff":
            item["display_label"] = _bounded_text(
                getattr(conversation, "visitor_name", None),
                160,
            ) or "实时会话"
            intent = _bounded_text(getattr(conversation, "last_intent", None), 160)
            item["display_summary"] = intent or "客户实时会话"
        elif source_type == "dispatch":
            item["display_label"] = "内部任务"
            item["display_summary"] = "内部派发任务"
        else:
            item["display_label"] = "客服任务"
            item["display_summary"] = None

    return result
