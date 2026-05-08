from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, inspect, or_, select, text
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    Customer,
    Team,
    Ticket,
    TicketAIIntake,
    TicketAttachment,
    TicketComment,
    TicketEvent,
    TicketInternalNote,
    TicketOutboundMessage,
    User,
)
from ..services.permissions import ensure_ticket_visible
from ..utils.time import utc_now
from ..webchat_models import WebchatEvent
from .deps import get_current_user

router = APIRouter(prefix="/api/tickets", tags=["tickets"])

DEFAULT_TIMELINE_LIMIT = 50
MAX_TIMELINE_LIMIT = 100
SOURCE_ORDER = {
    "comment": 0,
    "internal_note": 1,
    "outbound_message": 2,
    "ai_intake": 3,
    "ticket_event": 4,
    "webchat_event": 5,
}


def _value(value: Any) -> Any:
    if hasattr(value, "value"):
        return value.value
    return value


def _dt(value: Any) -> str | None:
    return value.isoformat() if value else None


def _safe_limit(limit: int | None) -> int:
    return max(1, min(int(limit or DEFAULT_TIMELINE_LIMIT), MAX_TIMELINE_LIMIT))


def _customer_summary(row: Customer | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "name": row.name,
        "email": row.email,
        "phone": row.phone,
        "external_ref": row.external_ref,
    }


def _user_summary(row: User | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "username": row.username,
        "display_name": row.display_name,
        "role": _value(row.role),
    }


def _team_summary(row: Team | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {"id": row.id, "name": row.name}


def _load_ticket_tags(db: Session, ticket_id: int) -> list[dict[str, Any]]:
    """Best-effort tag summary without importing optional tag models.

    The legacy detail serializer resolves tags elsewhere. This summary endpoint
    intentionally avoids relationship loading and only reads known lightweight
    tag table shapes when they exist.
    """

    try:
        bind = db.get_bind()
        inspector = inspect(bind)
        tables = set(inspector.get_table_names())
        rows: list[Any] = []
        if "ticket_tags" in tables:
            cols = {item["name"] for item in inspector.get_columns("ticket_tags")}
            if {"ticket_id", "name"}.issubset(cols):
                color_expr = "color" if "color" in cols else "NULL AS color"
                id_expr = "id" if "id" in cols else "NULL AS id"
                rows = db.execute(
                    text(f"SELECT {id_expr}, name, {color_expr} FROM ticket_tags WHERE ticket_id = :ticket_id ORDER BY name ASC"),
                    {"ticket_id": ticket_id},
                ).mappings().all()
            elif {"ticket_id", "tag_id"}.issubset(cols) and "tags" in tables:
                tag_cols = {item["name"] for item in inspector.get_columns("tags")}
                color_expr = "t.color" if "color" in tag_cols else "NULL AS color"
                rows = db.execute(
                    text(
                        f"SELECT t.id, t.name, {color_expr} FROM ticket_tags tt "
                        "JOIN tags t ON t.id = tt.tag_id WHERE tt.ticket_id = :ticket_id ORDER BY t.name ASC"
                    ),
                    {"ticket_id": ticket_id},
                ).mappings().all()
        elif "ticket_tag_links" in tables and "tags" in tables:
            link_cols = {item["name"] for item in inspector.get_columns("ticket_tag_links")}
            tag_cols = {item["name"] for item in inspector.get_columns("tags")}
            if {"ticket_id", "tag_id"}.issubset(link_cols):
                color_expr = "t.color" if "color" in tag_cols else "NULL AS color"
                rows = db.execute(
                    text(
                        f"SELECT t.id, t.name, {color_expr} FROM ticket_tag_links ttl "
                        "JOIN tags t ON t.id = ttl.tag_id WHERE ttl.ticket_id = :ticket_id ORDER BY t.name ASC"
                    ),
                    {"ticket_id": ticket_id},
                ).mappings().all()
        return [{"id": row.get("id"), "name": row.get("name"), "color": row.get("color")} for row in rows]
    except Exception:
        return []


def _counts(db: Session, ticket_id: int) -> dict[str, int]:
    stmt = select(
        select(func.count(TicketComment.id)).where(TicketComment.ticket_id == ticket_id).scalar_subquery().label("comments_count"),
        select(func.count(TicketInternalNote.id)).where(TicketInternalNote.ticket_id == ticket_id).scalar_subquery().label("internal_notes_count"),
        select(func.count(TicketAttachment.id)).where(TicketAttachment.ticket_id == ticket_id).scalar_subquery().label("attachments_count"),
        select(func.count(TicketOutboundMessage.id)).where(TicketOutboundMessage.ticket_id == ticket_id).scalar_subquery().label("outbound_messages_count"),
        select(func.count(TicketAIIntake.id)).where(TicketAIIntake.ticket_id == ticket_id).scalar_subquery().label("ai_intakes_count"),
        select(func.count(TicketEvent.id)).where(TicketEvent.ticket_id == ticket_id).scalar_subquery().label("events_count"),
    )
    row = db.execute(stmt).mappings().one()
    return {key: int(row[key] or 0) for key in row.keys()}


@router.get("/{ticket_id}/summary")
def get_ticket_summary(ticket_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)):
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

    counts = _counts(db, ticket_id)
    latest_ai = (
        db.query(TicketAIIntake)
        .filter(TicketAIIntake.ticket_id == ticket_id)
        .order_by(TicketAIIntake.created_at.desc(), TicketAIIntake.id.desc())
        .first()
    )
    latest_outbound = (
        db.query(TicketOutboundMessage)
        .filter(TicketOutboundMessage.ticket_id == ticket_id)
        .order_by(TicketOutboundMessage.created_at.desc(), TicketOutboundMessage.id.desc())
        .first()
    )
    latest_event = (
        db.query(TicketEvent)
        .filter(TicketEvent.ticket_id == ticket_id)
        .order_by(TicketEvent.created_at.desc(), TicketEvent.id.desc())
        .first()
    )
    now = utc_now()
    first_due = ticket.first_response_due_at
    resolution_due = ticket.resolution_due_at
    overdue = bool((first_due and first_due < now and not ticket.first_response_at) or (resolution_due and resolution_due < now and not ticket.resolved_at))
    customer_data = _customer_summary(customer)
    assignee_data = _user_summary(assignee)
    team_data = _team_summary(team)
    payload = {
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
        "created_at": _dt(ticket.created_at),
        "updated_at": _dt(ticket.updated_at),
        "first_response_due_at": _dt(ticket.first_response_due_at),
        "resolution_due_at": _dt(ticket.resolution_due_at),
        "first_response_breached": ticket.first_response_breached,
        "resolution_breached": ticket.resolution_breached,
        "customer": customer_data,
        "assignee": assignee_data,
        "team": team_data,
        "sla": {
            "overdue": overdue,
            "first_response_due_at": _dt(ticket.first_response_due_at),
            "resolution_due_at": _dt(ticket.resolution_due_at),
            "first_response_breached": ticket.first_response_breached,
            "resolution_breached": ticket.resolution_breached,
        },
        "tags": _load_ticket_tags(db, ticket_id),
        "counts": counts,
        "latest_ai_summary": latest_ai.summary if latest_ai else ticket.ai_summary,
        "latest_outbound_status": _value(latest_outbound.status) if latest_outbound else None,
        "latest_timeline_event": {
            "id": latest_event.id,
            "event_type": _value(latest_event.event_type),
            "created_at": _dt(latest_event.created_at),
        } if latest_event else None,
        # Compatibility fields used by the current workspace first paint.
        "customer_name": customer.name if customer else None,
        "assignee_name": assignee.display_name if assignee else None,
        "team_name": team.name if team else None,
        "market_code": None,
        "country_code": ticket.country_code,
        "ai_summary": ticket.ai_summary,
        "ai_classification": ticket.ai_classification,
        "preferred_reply_channel": ticket.preferred_reply_channel,
        "preferred_reply_contact": ticket.preferred_reply_contact,
        "openclaw_transcript": [],
        "attachments": [],
        "openclaw_attachment_references": [],
        "active_market_bulletins": [],
    }
    payload.update(counts)
    return payload


def _encode_timeline_cursor(item: dict[str, Any]) -> str:
    raw = json.dumps(
        {"created_at": item.get("created_at"), "source_type": item.get("source_type"), "source_id": item.get("source_id")},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _parse_cursor(cursor: str | None) -> tuple[datetime, int, int] | None:
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        created_raw = str(data["created_at"])
        created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        source_type = str(data["source_type"])
        source_order = SOURCE_ORDER[source_type]
        source_id = int(data["source_id"])
        return created, source_order, source_id
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid cursor") from exc


def _item_key(item: dict[str, Any]) -> tuple[datetime, int, int]:
    created_raw = item.get("created_at")
    if isinstance(created_raw, datetime):
        created = created_raw
    else:
        created = datetime.fromisoformat(str(created_raw).replace("Z", "+00:00"))
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return (created, -SOURCE_ORDER[item["source_type"]], int(item["source_id"]))


def _older_than_cursor(item: dict[str, Any], cursor_key: tuple[datetime, int, int] | None) -> bool:
    if cursor_key is None:
        return True
    created, neg_order, source_id = _item_key(item)
    return (created, -neg_order, source_id) < cursor_key


def _base_timeline_query(query, model, ticket_id: int, cursor_key: tuple[datetime, int, int] | None, limit: int):
    if cursor_key is not None:
        created_at = cursor_key[0]
        query = query.filter(or_(model.created_at <= created_at, model.created_at.is_(None)))
    return query.filter(model.ticket_id == ticket_id).order_by(model.created_at.desc(), model.id.desc()).limit(limit + 1).all()


def _timeline_items(db: Session, ticket_id: int, cursor_key: tuple[datetime, int, int] | None, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in _base_timeline_query(db.query(TicketComment), TicketComment, ticket_id, cursor_key, limit):
        item = {"source_type": "comment", "source_id": row.id, "id": f"comment:{row.id}", "created_at": _dt(row.created_at), "body": row.body, "visibility": _value(row.visibility), "author_id": row.author_id}
        if _older_than_cursor(item, cursor_key):
            items.append(item)
    for row in _base_timeline_query(db.query(TicketInternalNote), TicketInternalNote, ticket_id, cursor_key, limit):
        item = {"source_type": "internal_note", "source_id": row.id, "id": f"internal_note:{row.id}", "created_at": _dt(row.created_at), "body": row.body, "visibility": "internal", "author_id": row.author_id}
        if _older_than_cursor(item, cursor_key):
            items.append(item)
    for row in _base_timeline_query(db.query(TicketOutboundMessage), TicketOutboundMessage, ticket_id, cursor_key, limit):
        item = {"source_type": "outbound_message", "source_id": row.id, "id": f"outbound_message:{row.id}", "created_at": _dt(row.created_at), "body": row.body, "status": _value(row.status), "channel": _value(row.channel), "created_by": row.created_by}
        if _older_than_cursor(item, cursor_key):
            items.append(item)
    for row in _base_timeline_query(db.query(TicketAIIntake), TicketAIIntake, ticket_id, cursor_key, limit):
        item = {"source_type": "ai_intake", "source_id": row.id, "id": f"ai_intake:{row.id}", "created_at": _dt(row.created_at), "summary": row.summary, "classification": row.classification, "confidence": row.confidence}
        if _older_than_cursor(item, cursor_key):
            items.append(item)
    for row in _base_timeline_query(db.query(TicketEvent), TicketEvent, ticket_id, cursor_key, limit):
        item = {"source_type": "ticket_event", "source_id": row.id, "id": f"ticket_event:{row.id}", "created_at": _dt(row.created_at), "event_type": _value(row.event_type), "field_name": row.field_name, "note": row.note}
        if _older_than_cursor(item, cursor_key):
            items.append(item)
    for row in _base_timeline_query(db.query(WebchatEvent), WebchatEvent, ticket_id, cursor_key, limit):
        item = {"source_type": "webchat_event", "source_id": row.id, "id": f"webchat_event:{row.id}", "created_at": _dt(row.created_at), "event_type": row.event_type}
        if _older_than_cursor(item, cursor_key):
            items.append(item)
    items.sort(key=_item_key, reverse=True)
    return items


@router.get("/{ticket_id}/timeline")
def get_ticket_timeline_page(
    ticket_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
    cursor: str | None = None,
    limit: int = Query(DEFAULT_TIMELINE_LIMIT, ge=1),
):
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    ensure_ticket_visible(current_user, ticket, db)
    safe_limit = _safe_limit(limit)
    cursor_key = _parse_cursor(cursor)
    rows = _timeline_items(db, ticket_id, cursor_key, safe_limit)
    visible = rows[:safe_limit]
    next_cursor = _encode_timeline_cursor(visible[-1]) if len(rows) > safe_limit and visible else None
    return {"items": visible, "next_cursor": next_cursor, "has_more": bool(next_cursor)}
