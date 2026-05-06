from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from ..enums import TicketStatus, UserRole
from ..models import Customer, Ticket, User
from ..utils.time import utc_now
from .sla_service import compute_sla_snapshot


def _ticket_list_item(ticket: Ticket) -> dict[str, Any]:
    return {
        "id": ticket.id,
        "ticket_no": ticket.ticket_no,
        "title": ticket.title,
        "status": ticket.status,
        "priority": ticket.priority,
        "source_channel": ticket.source_channel,
        "category": ticket.category,
        "sub_category": ticket.sub_category,
        "tracking_number": ticket.tracking_number,
        "customer_name": ticket.customer.name if ticket.customer else None,
        "assignee_name": ticket.assignee.display_name if ticket.assignee else None,
        "team_name": ticket.team.name if ticket.team else None,
        "updated_at": ticket.updated_at,
        "resolution_due_at": ticket.resolution_due_at,
        "overdue": compute_sla_snapshot(ticket).get("overdue", False),
    }


def list_tickets_page(
    db: Session,
    current_user: User,
    *,
    q: Optional[str] = None,
    status_value: Optional[str] = None,
    priority_value: Optional[str] = None,
    assignee_id: Optional[int] = None,
    team_id: Optional[int] = None,
    overdue: Optional[bool] = None,
    cursor: int | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    safe_limit = max(1, min(limit, 100))
    query = db.query(Ticket).options(joinedload(Ticket.customer), joinedload(Ticket.assignee), joinedload(Ticket.team))
    if current_user.role not in {UserRole.admin, UserRole.manager, UserRole.auditor}:
        query = query.filter(or_(Ticket.team_id == current_user.team_id, Ticket.assignee_id == current_user.id))

    if q:
        like = f"%{q}%"
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
    if priority_value:
        query = query.filter(Ticket.priority == priority_value)
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
    if cursor:
        query = query.filter(Ticket.id < cursor)

    rows = query.order_by(Ticket.id.desc()).limit(safe_limit + 1).all()
    visible = rows[:safe_limit]
    return {
        "items": [_ticket_list_item(ticket) for ticket in visible],
        "next_cursor": rows[safe_limit].id if len(rows) > safe_limit else None,
    }
