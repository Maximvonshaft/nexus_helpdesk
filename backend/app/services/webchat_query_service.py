from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..enums import ConversationState
from ..models import Ticket, User
from ..webchat_models import WebchatConversation, WebchatMessage
from .permissions import ensure_ticket_visible
from .webchat_ai_turn_service import ai_snapshot


def admin_list_conversations_page(
    db: Session,
    current_user: User,
    *,
    cursor: int | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Cursor-paginated WebChat inbox query.

    The existing `/admin/conversations` endpoint is kept for compatibility. This
    helper avoids the worst per-row last-message query by joining the max message
    id subquery and uses id cursor pagination for stable incremental reads.
    """
    safe_limit = max(1, min(limit, 100))
    last_message_subq = (
        db.query(
            WebchatMessage.conversation_id.label("conversation_id"),
            func.max(WebchatMessage.id).label("last_message_id"),
        )
        .group_by(WebchatMessage.conversation_id)
        .subquery()
    )
    last_message = WebchatMessage.__table__.alias("last_webchat_message")
    query = (
        db.query(WebchatConversation, Ticket, last_message.c.message_type, last_message.c.action_status)
        .join(Ticket, Ticket.id == WebchatConversation.ticket_id)
        .outerjoin(last_message_subq, last_message_subq.c.conversation_id == WebchatConversation.id)
        .outerjoin(last_message, last_message.c.id == last_message_subq.c.last_message_id)
    )
    if cursor:
        query = query.filter(WebchatConversation.id < cursor)
    rows = query.order_by(WebchatConversation.id.desc()).limit(safe_limit + 1).all()
    visible = rows[:safe_limit]

    items: list[dict[str, Any]] = []
    for conversation, ticket, last_message_type, last_action_status in visible:
        try:
            ensure_ticket_visible(current_user, ticket, db)
        except HTTPException:
            continue
        item = {
            "conversation_id": conversation.public_id,
            "cursor": conversation.id,
            "ticket_id": conversation.ticket_id,
            "ticket_no": ticket.ticket_no,
            "title": ticket.title,
            "status": ticket.status.value if hasattr(ticket.status, "value") else str(ticket.status),
            "visitor_name": conversation.visitor_name,
            "visitor_email": conversation.visitor_email,
            "visitor_phone": conversation.visitor_phone,
            "origin": conversation.origin,
            "page_url": conversation.page_url,
            "last_seen_at": conversation.last_seen_at.isoformat() if conversation.last_seen_at else None,
            "updated_at": conversation.updated_at.isoformat() if conversation.updated_at else None,
            "last_message_type": last_message_type,
            "last_action_status": last_action_status,
            "needs_human": ticket.conversation_state == ConversationState.human_review_required or bool(ticket.required_action),
        }
        item.update(ai_snapshot(conversation))
        items.append(item)

    next_cursor = rows[safe_limit][0].id if len(rows) > safe_limit else None
    return {"items": items, "next_cursor": next_cursor}
