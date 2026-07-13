from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import and_, false, or_
from sqlalchemy.orm import Query, Session

from ..enums import UserRole
from ..models import Ticket
from .tenant_authority import resolve_actor_tenant_id

_GLOBAL_SUPPORT_ROLES = {UserRole.admin, UserRole.auditor}


def support_ticket_scope_predicate(current_user: Any, *, actor_tenant_id: int | None):
    """Return one Tenant-contained Ticket predicate for support reads.

    An authenticated relational Tenant is always the outer boundary, including
    privileged roles. In bounded shadow compatibility a fully unowned actor can
    see only fully unowned Ticket rows; it never widens into owned Tenant data.
    Legacy role/team/market rules are then applied inside that Tenant boundary.
    """

    tenant_predicate = (
        Ticket.tenant_id == actor_tenant_id
        if actor_tenant_id is not None
        else Ticket.tenant_id.is_(None)
    )
    role = getattr(current_user, "role", None)
    if role in _GLOBAL_SUPPORT_ROLES:
        return tenant_predicate

    predicates = []
    user_id = getattr(current_user, "id", None)
    if user_id is not None:
        predicates.append(Ticket.assignee_id == user_id)

    team_id = getattr(current_user, "team_id", None)
    if team_id is not None:
        predicates.append(Ticket.team_id == team_id)

    if role == UserRole.manager:
        team = getattr(current_user, "team", None)
        market_id = getattr(team, "market_id", None) if team is not None else None
        if market_id is not None:
            predicates.append(Ticket.market_id == market_id)

    return and_(tenant_predicate, or_(*predicates)) if predicates else false()


def apply_support_ticket_scope(query: Query, current_user: Any, db: Session) -> Query:
    actor_tenant_id = resolve_actor_tenant_id(db, current_user)
    return query.filter(
        support_ticket_scope_predicate(
            current_user,
            actor_tenant_id=actor_tenant_id,
        )
    )


def ensure_support_ticket_visible(db: Session, current_user: Any, ticket_id: int) -> None:
    visible = apply_support_ticket_scope(
        db.query(Ticket.id).filter(Ticket.id == int(ticket_id)),
        current_user,
        db,
    ).first()
    if visible is None:
        # Deliberately use 404 so an unauthorized actor cannot distinguish a
        # hidden support case from a non-existent one.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="support_conversation_not_found",
        )
