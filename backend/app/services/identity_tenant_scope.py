from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Query, Session

from ..models import Market, Team, User
from .tenant_authority import (
    ensure_resource_tenant,
    ensure_team_tenant,
    ensure_user_tenant,
    resolve_actor_tenant_id,
)


def actor_tenant_id(db: Session, actor: User) -> int | None:
    """Resolve the sole server-owned Tenant for an identity administrator."""

    return resolve_actor_tenant_id(db, actor)


def apply_tenant_scope(query: Query, model: Any, tenant_id: int | None) -> Query:
    """Restrict an ORM query to the actor Tenant or the fully legacy shadow scope."""

    tenant_column = getattr(model, "tenant_id")
    return query.filter(tenant_column == tenant_id) if tenant_id is not None else query.filter(tenant_column.is_(None))


def user_for_actor(db: Session, actor_tenant: int | None, user_id: int) -> User | None:
    row = db.query(User).filter(User.id == user_id).first()
    if row is None:
        return None
    ensure_user_tenant(db, actor_tenant, row)
    return row


def team_for_actor(db: Session, actor_tenant: int | None, team_id: int) -> Team | None:
    row = db.query(Team).filter(Team.id == team_id).first()
    if row is None:
        return None
    ensure_team_tenant(db, actor_tenant, row)
    return row


def active_team_for_actor(db: Session, actor_tenant: int | None, team_id: int) -> Team | None:
    row = db.query(Team).filter(Team.id == team_id, Team.is_active.is_(True)).first()
    if row is None:
        return None
    ensure_team_tenant(db, actor_tenant, row)
    return row


def active_market_for_actor(db: Session, actor_tenant: int | None, market_id: int) -> Market | None:
    row = db.query(Market).filter(Market.id == market_id, Market.is_active.is_(True)).first()
    if row is None:
        return None
    ensure_resource_tenant(db, actor_tenant, row, resource_kind="Market")
    return row
