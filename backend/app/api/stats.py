from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Ticket
from ..schemas import TicketStatsRead
from ..services.ticket_service import get_ticket_stats
from ..services.webchat_fast_idempotency_db import WebchatFastIdempotency
from ..utils.time import utc_now
from ..webchat_models import WebchatConversation, WebchatMessage
from .deps import get_current_user

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/tickets", response_model=TicketStatsRead)
def ticket_stats(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    return get_ticket_stats(db, current_user)


def _rows_to_int_map(rows) -> dict[str, int]:
    return {str(key or "unknown"): int(count or 0) for key, count in rows}


@router.get("/webchat-fast")
def webchat_fast_stats(
    days: int = Query(default=7, ge=1, le=90),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Operational visibility for ticketless-first Fast Lane traffic.

    Ticket volume alone is no longer a valid proxy for support demand because
    AI-resolved WebChat sessions deliberately do not create tickets. This endpoint
    exposes the deflection/handoff counters needed to prove that business loop.
    """

    since = utc_now() - timedelta(days=days)
    base_conversations = db.query(WebchatConversation).filter(
        WebchatConversation.origin == "webchat-fast",
        WebchatConversation.created_at >= since,
    )
    total_sessions = base_conversations.count()
    ticketless_sessions = base_conversations.filter(WebchatConversation.ticket_id.is_(None)).count()
    handoff_sessions = base_conversations.filter(WebchatConversation.ticket_id.is_not(None)).count()
    ai_resolved_sessions = (
        db.query(func.count(func.distinct(WebchatMessage.conversation_id)))
        .join(WebchatConversation, WebchatConversation.id == WebchatMessage.conversation_id)
        .filter(
            WebchatConversation.origin == "webchat-fast",
            WebchatConversation.ticket_id.is_(None),
            WebchatMessage.direction == "ai",
            WebchatMessage.created_at >= since,
        )
        .scalar()
        or 0
    )
    customer_messages = (
        db.query(WebchatMessage)
        .join(WebchatConversation, WebchatConversation.id == WebchatMessage.conversation_id)
        .filter(
            WebchatConversation.origin == "webchat-fast",
            WebchatMessage.direction == "visitor",
            WebchatMessage.created_at >= since,
        )
        .count()
    )
    ai_messages = (
        db.query(WebchatMessage)
        .join(WebchatConversation, WebchatConversation.id == WebchatMessage.conversation_id)
        .filter(
            WebchatConversation.origin == "webchat-fast",
            WebchatMessage.direction == "ai",
            WebchatMessage.created_at >= since,
        )
        .count()
    )
    system_handoff_messages = (
        db.query(WebchatMessage)
        .join(WebchatConversation, WebchatConversation.id == WebchatMessage.conversation_id)
        .filter(
            WebchatConversation.origin == "webchat-fast",
            WebchatMessage.direction == "system",
            WebchatMessage.created_at >= since,
        )
        .count()
    )
    tickets_created = (
        db.query(Ticket)
        .filter(
            Ticket.source_chat_id.ilike("webchat-fast:%"),
            Ticket.created_at >= since,
        )
        .count()
    )
    status_rows = (
        db.query(WebchatFastIdempotency.status, func.count(WebchatFastIdempotency.id))
        .filter(WebchatFastIdempotency.created_at >= since)
        .group_by(WebchatFastIdempotency.status)
        .all()
    )
    error_rows = (
        db.query(WebchatFastIdempotency.error_code, func.count(WebchatFastIdempotency.id))
        .filter(WebchatFastIdempotency.created_at >= since, WebchatFastIdempotency.error_code.is_not(None))
        .group_by(WebchatFastIdempotency.error_code)
        .all()
    )
    intent_rows = (
        db.query(WebchatConversation.last_intent, func.count(WebchatConversation.id))
        .filter(WebchatConversation.origin == "webchat-fast", WebchatConversation.created_at >= since)
        .group_by(WebchatConversation.last_intent)
        .all()
    )
    return {
        "days": days,
        "since": since.isoformat(),
        "total_sessions": int(total_sessions),
        "ticketless_sessions": int(ticketless_sessions),
        "ai_resolved_sessions": int(ai_resolved_sessions),
        "handoff_sessions": int(handoff_sessions),
        "handoff_rate": float(handoff_sessions / total_sessions) if total_sessions else 0.0,
        "customer_messages": int(customer_messages),
        "ai_messages": int(ai_messages),
        "system_handoff_messages": int(system_handoff_messages),
        "tickets_created": int(tickets_created),
        "idempotency_by_status": _rows_to_int_map(status_rows),
        "errors_by_code": _rows_to_int_map(error_rows),
        "sessions_by_intent": _rows_to_int_map(intent_rows),
    }
