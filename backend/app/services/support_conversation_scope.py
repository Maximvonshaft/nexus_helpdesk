from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import false, or_, true
from sqlalchemy.orm import Query, Session

from ..enums import UserRole
from ..models import Ticket

_GLOBAL_SUPPORT_ROLES = {UserRole.admin, UserRole.auditor}


def support_ticket_scope_predicate(current_user: Any):
    """Return the authoritative Ticket predicate for support-conversation reads.

    Admin and Auditor retain the intentional global control/audit view. Manager
    is limited to the actor's market, team, or direct assignments. Lead and Agent
    are limited to their team or direct assignments. Missing organizational
    provenance fails closed instead of widening access.
    """

    role = getattr(current_user, "role", None)
    if role in _GLOBAL_SUPPORT_ROLES:
        return true()

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

    return or_(*predicates) if predicates else false()


def apply_support_ticket_scope(query: Query, current_user: Any) -> Query:
    return query.filter(support_ticket_scope_predicate(current_user))


def ensure_support_ticket_visible(db: Session, current_user: Any, ticket_id: int) -> None:
    visible = apply_support_ticket_scope(
        db.query(Ticket.id).filter(Ticket.id == int(ticket_id)),
        current_user,
    ).first()
    if visible is None:
        # Deliberately use 404 so an unauthorized actor cannot distinguish a
        # hidden support case from a non-existent one.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="support_conversation_not_found",
        )
