from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session, joinedload

from ..enums import SourceChannel, TicketStatus, UserRole
from ..models import Customer, Ticket, User
from ..utils.time import utc_now
from .lite_service import serialize_lite_list

DEFAULT_LIMIT = 50
MAX_LIMIT = 100
MIN_SEARCH_CHARS = 3
MAX_SEARCH_CHARS = 80


def _safe_limit(limit: int | None) -> int:
    return max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))


def _encode_cursor(*, updated_at: datetime | None, ticket_id: int) -> str:
    raw = json.dumps(
        {
            "updated_at": updated_at.isoformat() if updated_at else None,
            "id": int(ticket_id),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None) -> tuple[datetime | None, int] | None:
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        parsed = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        updated_raw = parsed.get("updated_at")
        updated_at = datetime.fromisoformat(updated_raw) if updated_raw else None
        ticket_id = int(parsed["id"])
        return updated_at, ticket_id
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc


def _lite_status_filter(status: str | None) -> tuple[str | None, list[str] | None]:
    if not status:
        return None, None
    if status == "pending_human":
        return None, [
            TicketStatus.new.value,
            TicketStatus.pending_assignment.value,
            TicketStatus.waiting_internal.value,
            TicketStatus.escalated.value,
        ]
    if status == "closed":
        active = {
            TicketStatus.new,
            TicketStatus.pending_assignment,
            TicketStatus.waiting_internal,
            TicketStatus.escalated,
            TicketStatus.in_progress,
            TicketStatus.waiting_customer,
            TicketStatus.resolved,
        }
        return None, [s.value for s in TicketStatus if s not in active]
    mapping = {
        "new": TicketStatus.new,
        "in_progress": TicketStatus.in_progress,
        "waiting_customer": TicketStatus.waiting_customer,
        "resolved": TicketStatus.resolved,
    }
    internal = mapping.get(status)
    if not internal:
        raise HTTPException(status_code=400, detail="Unsupported status")
    return internal.value, None


def _source_channel_filter(source_channel: str | None) -> SourceChannel | None:
    if not source_channel:
        return None
    try:
        return SourceChannel(str(source_channel).strip().lower())
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Unsupported source_channel") from exc


def _normalize_q(q: str | None) -> str | None:
    if q is None:
        return None
    value = " ".join(str(q).strip().split())
    if not value:
        return None
    if len(value) < MIN_SEARCH_CHARS:
        raise HTTPException(status_code=400, detail=f"q must be at least {MIN_SEARCH_CHARS} characters")
    if len(value) > MAX_SEARCH_CHARS:
        raise HTTPException(status_code=400, detail=f"q must be at most {MAX_SEARCH_CHARS} characters")
    return value


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _ticket_overdue(ticket: Ticket) -> bool:
    if ticket.resolution_due_at is None:
        return False
    if ticket.status in {TicketStatus.closed, TicketStatus.canceled}:
        return False
    return ticket.resolution_due_at < utc_now()


def _serialize_workspace_list_item(ticket: Ticket) -> dict[str, Any]:
    customer = ticket.customer
    assignee = ticket.assignee
    team = ticket.team
    market = ticket.market
    return {
        "id": ticket.id,
        "ticket_no": ticket.ticket_no,
        "title": ticket.issue_summary or ticket.title,
        "status": _enum_value(ticket.status),
        "priority": _enum_value(ticket.priority),
        "source_channel": _enum_value(ticket.source_channel),
        "category": ticket.category,
        "sub_category": ticket.sub_category,
        "tracking_number": ticket.tracking_number,
        "customer_name": customer.name if customer else None,
        "assignee_name": assignee.display_name if assignee else None,
        "team_name": team.name if team else None,
        "market_id": ticket.market_id,
        "market_code": market.code if market else None,
        "country_code": ticket.country_code,
        "conversation_state": _enum_value(ticket.conversation_state),
        "updated_at": ticket.updated_at.isoformat() if ticket.updated_at else None,
        "resolution_due_at": ticket.resolution_due_at.isoformat() if ticket.resolution_due_at else None,
        "overdue": _ticket_overdue(ticket),
    }


def list_lite_cases_page(
    db: Session,
    current_user: User,
    *,
    q: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    assignee_id: int | None = None,
    team_id: int | None = None,
    overdue: bool | None = None,
    source_channel: str | None = None,
    cursor: str | None = None,
    limit: int | None = DEFAULT_LIMIT,
) -> dict[str, Any]:
    safe_limit = _safe_limit(limit)
    status_value, status_in = _lite_status_filter(status)
    source_channel_value = _source_channel_filter(source_channel)
    normalized_q = _normalize_q(q)

    query = db.query(Ticket).options(
        joinedload(Ticket.customer),
        joinedload(Ticket.assignee),
        joinedload(Ticket.team),
        joinedload(Ticket.market),
    )
    if current_user.role not in {UserRole.admin, UserRole.manager, UserRole.auditor}:
        query = query.filter(or_(Ticket.team_id == current_user.team_id, Ticket.assignee_id == current_user.id))

    if normalized_q:
        like = f"%{normalized_q}%"
        query = query.outerjoin(Customer, Customer.id == Ticket.customer_id).filter(
            or_(
                Ticket.ticket_no.ilike(like),
                Ticket.title.ilike(like),
                Ticket.description.ilike(like),
                Customer.name.ilike(like),
                Ticket.tracking_number.ilike(like),
            )
        )
    if status_value:
        query = query.filter(Ticket.status == status_value)
    if status_in:
        query = query.filter(Ticket.status.in_(status_in))
    if priority:
        query = query.filter(Ticket.priority == priority)
    if assignee_id:
        query = query.filter(Ticket.assignee_id == assignee_id)
    if team_id:
        query = query.filter(Ticket.team_id == team_id)
    if overdue is True:
        query = query.filter(
            Ticket.resolution_due_at.is_not(None),
            Ticket.resolution_due_at < utc_now(),
            Ticket.status.notin_([TicketStatus.closed, TicketStatus.canceled]),
        )
    if source_channel_value:
        query = query.filter(Ticket.source_channel == source_channel_value)

    decoded = _decode_cursor(cursor)
    if decoded:
        cursor_updated_at, cursor_id = decoded
        if cursor_updated_at is None:
            query = query.filter(Ticket.id < cursor_id)
        else:
            query = query.filter(
                or_(
                    Ticket.updated_at < cursor_updated_at,
                    and_(Ticket.updated_at == cursor_updated_at, Ticket.id < cursor_id),
                )
            )

    rows = query.order_by(Ticket.updated_at.desc(), Ticket.id.desc()).limit(safe_limit + 1).all()
    visible = rows[:safe_limit]
    has_more = len(rows) > safe_limit
    next_cursor = None
    if has_more and visible:
        last = visible[-1]
        next_cursor = _encode_cursor(updated_at=last.updated_at, ticket_id=last.id)

    return {
        "items": [_serialize_workspace_list_item(ticket) for ticket in visible],
        "next_cursor": next_cursor,
        "has_more": has_more,
        "filters": {
            "q": normalized_q,
            "status": status,
            "priority": priority,
            "assignee_id": assignee_id,
            "team_id": team_id,
            "overdue": overdue,
            "source_channel": source_channel_value.value if source_channel_value else None,
            "limit": safe_limit,
        },
    }
