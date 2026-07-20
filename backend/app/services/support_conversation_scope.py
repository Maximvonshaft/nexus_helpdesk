from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import and_, exists, false, or_
from sqlalchemy.orm import Query, Session

from ..models import Ticket
from ..models_agent_routing import ConversationControl
from ..operator_models import OperatorQueueScopeGrant
from ..webchat_models import WebchatConversation
from .permissions import CAP_AUDIT_READ, CAP_TICKET_ASSIGN, CAP_USER_MANAGE, has_global_case_visibility, resolve_capabilities
from .tenant_authority import resolve_actor_tenant_id

_GLOBAL_SUPPORT_CAPABILITIES = {CAP_AUDIT_READ, CAP_USER_MANAGE}


def support_ticket_scope_predicate(
    current_user: Any,
    *,
    actor_tenant_id: int | None,
    capabilities: set[str] | None = None,
):
    """Return one tenant-contained, capability-derived Ticket predicate."""

    tenant_predicate = (
        Ticket.tenant_id == actor_tenant_id
        if actor_tenant_id is not None
        else Ticket.tenant_id.is_(None)
    )
    effective = capabilities or set()
    if effective & _GLOBAL_SUPPORT_CAPABILITIES:
        return tenant_predicate

    predicates = []
    user_id = getattr(current_user, "id", None)
    if user_id is not None:
        predicates.append(Ticket.assignee_id == user_id)

    team_id = getattr(current_user, "team_id", None)
    if team_id is not None:
        predicates.append(Ticket.team_id == team_id)

    if CAP_TICKET_ASSIGN in effective:
        team = getattr(current_user, "team", None)
        market_id = getattr(team, "market_id", None) if team is not None else None
        if market_id is not None:
            predicates.append(Ticket.market_id == market_id)

    return and_(tenant_predicate, or_(*predicates)) if predicates else false()


def apply_support_ticket_scope(query: Query, current_user: Any, db: Session) -> Query:
    actor_tenant_id = resolve_actor_tenant_id(db, current_user)
    capabilities = resolve_capabilities(current_user, db)
    return query.filter(
        support_ticket_scope_predicate(
            current_user,
            actor_tenant_id=actor_tenant_id,
            capabilities=capabilities,
        )
    )



def apply_support_conversation_scope(
    query: Query,
    current_user: Any,
    db: Session,
) -> Query:
    """Apply one SQL-level scope across ticket-backed and ticketless conversations."""

    actor_tenant_id = resolve_actor_tenant_id(db, current_user)
    capabilities = resolve_capabilities(current_user, db)
    ticket_scope = support_ticket_scope_predicate(
        current_user,
        actor_tenant_id=actor_tenant_id,
        capabilities=capabilities,
    )
    if has_global_case_visibility(current_user, db):
        ticketless_scope = Ticket.id.is_(None)
    else:
        user_id = getattr(current_user, "id", None)
        if user_id is None:
            ticketless_scope = false()
        else:
            ticketless_scope = and_(
                Ticket.id.is_(None),
                exists().where(
                    and_(
                        ConversationControl.conversation_id == WebchatConversation.id,
                        OperatorQueueScopeGrant.user_id == user_id,
                        OperatorQueueScopeGrant.enabled.is_(True),
                        OperatorQueueScopeGrant.tenant_key == ConversationControl.tenant_key,
                        OperatorQueueScopeGrant.country_code == ConversationControl.country_code,
                        OperatorQueueScopeGrant.channel_key == ConversationControl.channel_key,
                    )
                ),
            )
    return query.filter(or_(ticket_scope, ticketless_scope))

def ensure_support_ticket_visible(db: Session, current_user: Any, ticket_id: int) -> None:
    visible = apply_support_ticket_scope(
        db.query(Ticket.id).filter(Ticket.id == int(ticket_id)),
        current_user,
        db,
    ).first()
    if visible is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="support_conversation_not_found",
        )
