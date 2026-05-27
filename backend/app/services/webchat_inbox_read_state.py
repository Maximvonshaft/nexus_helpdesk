from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from ..models import Ticket, User
from ..utils.time import utc_now
from ..webchat_models import WebchatConversation, WebchatEvent, WebchatInboxReadState
from .permissions import ensure_ticket_visible

UNREAD_EVENT_TYPES = {
    "message.created",
    "handoff.requested",
    "handoff.request_updated",
    "handoff.force_takeover",
    "handoff.released",
    "ai.resumed",
}


def _last_event_id(db: Session, conversation_id: int) -> int:
    value = (
        db.query(func.max(WebchatEvent.id))
        .filter(WebchatEvent.conversation_id == conversation_id)
        .scalar()
    )
    return int(value or 0)


def _read_state(db: Session, *, conversation_id: int, user_id: int) -> WebchatInboxReadState | None:
    return (
        db.query(WebchatInboxReadState)
        .filter(
            WebchatInboxReadState.conversation_id == conversation_id,
            WebchatInboxReadState.user_id == user_id,
        )
        .first()
    )


def _unread_count(db: Session, *, conversation_id: int, after_event_id: int) -> int:
    value = (
        db.query(func.count(WebchatEvent.id))
        .filter(
            WebchatEvent.conversation_id == conversation_id,
            WebchatEvent.id > max(0, int(after_event_id or 0)),
            WebchatEvent.event_type.in_(UNREAD_EVENT_TYPES),
        )
        .scalar()
    )
    return int(value or 0)


def webchat_read_state_payload(db: Session, *, conversation_id: int, user_id: int) -> dict[str, Any]:
    last_event_id = _last_event_id(db, conversation_id)
    state = _read_state(db, conversation_id=conversation_id, user_id=user_id)
    if state is None:
        return {
            "last_event_id": last_event_id,
            "last_read_event_id": last_event_id,
            "marked_unread": False,
            "unread_count": 0,
        }
    count = _unread_count(db, conversation_id=conversation_id, after_event_id=state.last_read_event_id)
    if state.marked_unread:
        count = max(count, 1)
    return {
        "last_event_id": last_event_id,
        "last_read_event_id": int(state.last_read_event_id or 0),
        "marked_unread": bool(state.marked_unread),
        "unread_count": count,
    }


def webchat_read_state_payloads(db: Session, *, conversation_ids: list[int], user_id: int) -> dict[int, dict[str, Any]]:
    ids = [int(value) for value in conversation_ids if value]
    if not ids:
        return {}
    last_event_ids = {
        int(row.conversation_id): int(row.last_event_id or 0)
        for row in (
            db.query(WebchatEvent.conversation_id, func.max(WebchatEvent.id).label("last_event_id"))
            .filter(WebchatEvent.conversation_id.in_(ids))
            .group_by(WebchatEvent.conversation_id)
            .all()
        )
    }
    states = {
        int(row.conversation_id): row
        for row in (
            db.query(WebchatInboxReadState)
            .filter(WebchatInboxReadState.user_id == user_id, WebchatInboxReadState.conversation_id.in_(ids))
            .all()
        )
    }
    unread_counts = {
        int(row.conversation_id): int(row.unread_count or 0)
        for row in (
            db.query(WebchatEvent.conversation_id, func.count(WebchatEvent.id).label("unread_count"))
            .join(
                WebchatInboxReadState,
                and_(
                    WebchatInboxReadState.conversation_id == WebchatEvent.conversation_id,
                    WebchatInboxReadState.user_id == user_id,
                ),
            )
            .filter(
                WebchatEvent.conversation_id.in_(ids),
                WebchatEvent.event_type.in_(UNREAD_EVENT_TYPES),
                WebchatEvent.id > WebchatInboxReadState.last_read_event_id,
            )
            .group_by(WebchatEvent.conversation_id)
            .all()
        )
    }
    payloads: dict[int, dict[str, Any]] = {}
    for conversation_id in ids:
        last_event_id = last_event_ids.get(conversation_id, 0)
        state = states.get(conversation_id)
        if state is None:
            payloads[conversation_id] = {
                "last_event_id": last_event_id,
                "last_read_event_id": last_event_id,
                "marked_unread": False,
                "unread_count": 0,
            }
            continue
        unread_count = unread_counts.get(conversation_id, 0)
        if state.marked_unread:
            unread_count = max(unread_count, 1)
        payloads[conversation_id] = {
            "last_event_id": last_event_id,
            "last_read_event_id": int(state.last_read_event_id or 0),
            "marked_unread": bool(state.marked_unread),
            "unread_count": unread_count,
        }
    return payloads


def mark_webchat_read_state(
    db: Session,
    *,
    ticket_id: int,
    current_user: User,
    marked_unread: bool,
) -> dict[str, Any]:
    conversation = db.query(WebchatConversation).filter(WebchatConversation.ticket_id == ticket_id).first()
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="webchat conversation not found for ticket")
    ticket = db.query(Ticket).filter(Ticket.id == conversation.ticket_id).first()
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ticket not found")
    ensure_ticket_visible(current_user, ticket, db)

    state = _read_state(db, conversation_id=conversation.id, user_id=current_user.id)
    if state is None:
        state = WebchatInboxReadState(
            user_id=current_user.id,
            conversation_id=conversation.id,
            last_read_event_id=0,
            marked_unread=False,
        )
        db.add(state)

    state.last_read_event_id = _last_event_id(db, conversation.id)
    state.marked_unread = bool(marked_unread)
    state.updated_at = utc_now()
    db.flush()
    return {
        "conversation_id": conversation.public_id,
        "ticket_id": ticket.id,
        **webchat_read_state_payload(db, conversation_id=conversation.id, user_id=current_user.id),
    }
