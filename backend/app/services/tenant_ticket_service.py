from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from ..enums import TicketStatus, UserRole
from ..models import Customer, Ticket, User
from ..multi_tenant_models import TeamTenantLink, Tenant, TicketTenantLink
from ..schemas import TicketCreate
from .permissions import ensure_ticket_visible
from .tenant_service import attach_customer_to_tenant, attach_team_to_tenant, attach_ticket_to_tenant
from .ticket_service import create_ticket, get_ticket_or_404
from ..utils.time import utc_now

PRIVILEGED_ROLES = {UserRole.admin, UserRole.manager, UserRole.auditor}


def resolve_tenant_team_ids(db: Session, tenant_id: int) -> list[int]:
    return [row.team_id for row in db.query(TeamTenantLink.team_id).filter(TeamTenantLink.tenant_id == tenant_id).all()]


def ensure_ticket_in_tenant(db: Session, tenant_id: int, ticket_id: int) -> Ticket:
    ticket = (
        db.query(Ticket)
        .join(TicketTenantLink, TicketTenantLink.ticket_id == Ticket.id)
        .options(joinedload(Ticket.customer), joinedload(Ticket.assignee), joinedload(Ticket.team))
        .filter(TicketTenantLink.tenant_id == tenant_id, Ticket.id == ticket_id)
        .first()
    )
    if ticket is None:
        raise HTTPException(status_code=404, detail='Ticket not found in current tenant')
    return ticket


def ensure_ticket_visible_in_tenant(db: Session, current_user: User, current_tenant: Tenant, ticket_id: int) -> Ticket:
    ticket = ensure_ticket_in_tenant(db, current_tenant.id, ticket_id)
    ensure_ticket_visible(current_user, ticket, db)
    return ticket


def list_tenant_tickets(
    db: Session,
    current_user: User,
    current_tenant: Tenant,
    *,
    q: str | None = None,
    status_value: str | None = None,
    status_in: list[str] | None = None,
    priority_value: str | None = None,
    assignee_id: int | None = None,
    team_id: int | None = None,
    overdue: bool | None = None,
    limit: int = 50,
    skip: int = 0,
) -> list[Ticket]:
    query = (
        db.query(Ticket)
        .join(TicketTenantLink, TicketTenantLink.ticket_id == Ticket.id)
        .options(joinedload(Ticket.customer), joinedload(Ticket.assignee), joinedload(Ticket.team))
        .filter(TicketTenantLink.tenant_id == current_tenant.id)
    )

    if current_user.role not in PRIVILEGED_ROLES:
        tenant_team_ids = resolve_tenant_team_ids(db, current_tenant.id)
        if tenant_team_ids:
            query = query.filter(or_(Ticket.assignee_id == current_user.id, Ticket.team_id.in_(tenant_team_ids)))
        else:
            query = query.filter(Ticket.assignee_id == current_user.id)

    if q:
        like = f'%{q}%'
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
        query = query.filter(Ticket.resolution_due_at.is_not(None), Ticket.resolution_due_at < utc_now(), Ticket.status.notin_([TicketStatus.closed, TicketStatus.canceled]))
    return query.order_by(Ticket.updated_at.desc()).offset(skip).limit(limit).all()


def create_tenant_ticket(db: Session, current_user: User, current_tenant: Tenant, payload: TicketCreate) -> Ticket:
    ticket = create_ticket(db, payload, current_user)
    attach_ticket_to_tenant(db, ticket_id=ticket.id, tenant_id=current_tenant.id)
    if ticket.customer_id:
        attach_customer_to_tenant(db, customer_id=ticket.customer_id, tenant_id=current_tenant.id)
    if ticket.team_id:
        attach_team_to_tenant(db, team_id=ticket.team_id, tenant_id=current_tenant.id)
    db.flush()
    return get_ticket_or_404(db, ticket.id)
