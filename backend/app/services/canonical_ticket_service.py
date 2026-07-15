from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from ..enums import TicketStatus
from ..models import Customer, Ticket, User
from ..utils.time import utc_now
from . import ticket_service_core as _core
from .scope_permissions import has_global_case_visibility
from .tenant_authority import (
    ensure_team_tenant,
    ensure_ticket_tenant_authority,
    ensure_user_tenant,
    resolve_actor_tenant_id,
)
from .ticket_service_core import *  # noqa: F401,F403


def validate_assignee_team(
    db: Session,
    assignee_id: Optional[int],
    team_id: Optional[int],
    *,
    fallback_team_id: Optional[int] = None,
    actor_tenant_id: int | None = None,
):
    """Validate assignment without granting cross-team behavior by role name."""

    assignee = None
    team = None
    effective_team_id = team_id if team_id is not None else fallback_team_id
    if effective_team_id is not None:
        team = _core.get_team_or_404(db, effective_team_id)
    if assignee_id is not None:
        assignee = _core.get_user_or_404(db, assignee_id)
    if team is not None:
        ensure_team_tenant(db, actor_tenant_id, team)
    if assignee is not None:
        ensure_user_tenant(db, actor_tenant_id, assignee)
    if assignee and team and assignee.team_id != team.id:
        raise HTTPException(status_code=400, detail="Assignee does not belong to selected team")
    return assignee, team


_core.validate_assignee_team = validate_assignee_team


def list_tickets(
    db: Session,
    current_user: User,
    *,
    q: Optional[str] = None,
    status_value: Optional[str] = None,
    status_in: Optional[list[str]] = None,
    priority_value: Optional[str] = None,
    assignee_id: Optional[int] = None,
    team_id: Optional[int] = None,
    overdue: Optional[bool] = None,
    limit: int = 50,
    skip: int = 0,
) -> list[Ticket]:
    """Canonical list projection with capability-derived visibility."""

    actor_tenant_id = resolve_actor_tenant_id(db, current_user)
    query = db.query(Ticket).options(
        joinedload(Ticket.customer),
        joinedload(Ticket.assignee),
        joinedload(Ticket.creator),
        joinedload(Ticket.team),
        joinedload(Ticket.market),
        joinedload(Ticket.channel_account),
    )
    if actor_tenant_id is not None:
        query = query.filter(Ticket.tenant_id == actor_tenant_id)
    else:
        query = query.filter(Ticket.tenant_id.is_(None))
    if not has_global_case_visibility(current_user, db):
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
    if status_in:
        query = query.filter(Ticket.status.in_(status_in))
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
    tickets = query.order_by(Ticket.updated_at.desc()).offset(skip).limit(limit).all()
    for ticket in tickets:
        ensure_ticket_tenant_authority(
            db,
            current_user,
            ticket,
            actor_tenant_id=actor_tenant_id,
        )
    return tickets


def __getattr__(name: str):
    return getattr(_core, name)


__all__ = [
    "add_ai_intake",
    "add_attachment",
    "add_comment",
    "add_internal_note",
    "assign_ticket",
    "change_status",
    "create_ticket",
    "escalate_ticket",
    "get_ticket_events",
    "get_ticket_or_404",
    "list_tickets",
    "reopen_ticket",
    "save_outbound_draft",
    "send_outbound_message",
    "update_ticket",
    "validate_assignee_team",
]
