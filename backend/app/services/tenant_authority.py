from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..models import ChannelAccount, Customer, Market, Team, Tenant, Ticket, User
from ..settings import get_settings

RUNTIME_TENANT_ASSIGNMENT_SOURCE = "runtime_principal"
RUNTIME_TENANT_ASSIGNMENT_VERSION = "nexus.tenant.runtime_authority.v1"


def _detail(error_code: str, message: str) -> dict[str, str]:
    return {"error_code": error_code, "message": message}


def _raise(error_code: str, message: str, *, status_code: int) -> None:
    raise HTTPException(status_code=status_code, detail=_detail(error_code, message))


def tenant_runtime_authority_mode() -> str:
    return get_settings().tenant_runtime_authority_mode


def _active_tenant(db: Session, tenant_id: int, *, principal: bool) -> Tenant:
    tenant = db.get(Tenant, tenant_id)
    if tenant is None or not tenant.is_active:
        _raise(
            "tenant_principal_inactive" if principal else "tenant_resource_conflict",
            "Tenant authority is missing or inactive",
            status_code=status.HTTP_403_FORBIDDEN if principal else status.HTTP_409_CONFLICT,
        )
    return tenant


def _relation_tenant_id(value: Any) -> int | None:
    raw = getattr(value, "tenant_id", None)
    return int(raw) if raw is not None else None


def _linked_team(db: Session, user: User) -> Team | None:
    if user.team_id is None:
        return None
    team = db.query(Team).filter(Team.id == user.team_id).first()
    if team is None:
        _raise(
            "tenant_principal_conflict",
            "Authenticated principal Team relationship is missing",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    return team


def _linked_market(db: Session, team: Team | None) -> Market | None:
    if team is None or team.market_id is None:
        return None
    market = db.query(Market).filter(Market.id == team.market_id).first()
    if market is None:
        _raise(
            "tenant_principal_conflict",
            "Authenticated principal Market relationship is missing",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    return market


def resolve_actor_tenant_id(db: Session, user: User) -> int | None:
    """Return one active server-owned Tenant for an authenticated actor.

    Shadow mode preserves only a fully legacy chain where User, Team and Market all
    have no relational Tenant. Once any link is Tenant-bound, missing or conflicting
    ownership fails closed. Production defaults to enforce mode.
    """

    direct = _relation_tenant_id(user)
    team = _linked_team(db, user)
    market = _linked_market(db, team)
    related = [
        ("team", _relation_tenant_id(team) if team is not None else None),
        ("market", _relation_tenant_id(market) if market is not None else None),
    ]
    related_present = [tenant_id for _kind, tenant_id in related if tenant_id is not None]

    if direct is None:
        if tenant_runtime_authority_mode() == "enforce" or related_present:
            _raise(
                "tenant_principal_missing",
                "Authenticated principal has no authoritative Tenant",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        return None

    _active_tenant(db, direct, principal=True)
    for kind, tenant_id in related:
        relation_exists = team is not None if kind == "team" else market is not None
        if not relation_exists:
            continue
        if tenant_id is None:
            _raise(
                "tenant_principal_conflict",
                f"Authenticated principal {kind} ownership is missing",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        if tenant_id != direct:
            _raise(
                "tenant_principal_conflict",
                f"Authenticated principal {kind} ownership conflicts",
                status_code=status.HTTP_403_FORBIDDEN,
            )
    return direct


def ensure_resource_tenant(
    db: Session,
    actor_tenant_id: int | None,
    resource: Any,
    *,
    resource_kind: str,
    conceal_cross_tenant: bool = True,
) -> int | None:
    resource_tenant_id = _relation_tenant_id(resource)
    if resource_tenant_id is None:
        if actor_tenant_id is None and tenant_runtime_authority_mode() == "shadow":
            return None
        _raise(
            "tenant_resource_missing",
            f"{resource_kind} has no authoritative Tenant",
            status_code=status.HTTP_409_CONFLICT,
        )

    if actor_tenant_id is None or actor_tenant_id != resource_tenant_id:
        _raise(
            "tenant_resource_not_found",
            f"{resource_kind} is not visible for the authenticated Tenant",
            status_code=(
                status.HTTP_404_NOT_FOUND
                if conceal_cross_tenant
                else status.HTTP_403_FORBIDDEN
            ),
        )
    _active_tenant(db, resource_tenant_id, principal=False)
    return resource_tenant_id


def _ticket_relation(
    db: Session,
    ticket: Ticket,
    attribute: str,
    model: Any,
    id_attribute: str,
    *,
    relation_kind: str,
):
    value = getattr(ticket, attribute, None)
    relation_id = getattr(ticket, id_attribute, None)
    if value is not None:
        if relation_id is None or int(value.id) != int(relation_id):
            _raise(
                "tenant_resource_conflict",
                f"Ticket {relation_kind} relationship identity conflicts",
                status_code=status.HTTP_409_CONFLICT,
            )
        return value
    if relation_id is None:
        return None
    value = db.query(model).filter(model.id == relation_id).first()
    if value is None:
        _raise(
            "tenant_resource_conflict",
            f"Ticket {relation_kind} relationship is missing",
            status_code=status.HTTP_409_CONFLICT,
        )
    return value


_UNRESOLVED_ACTOR_TENANT = object()


def ensure_ticket_tenant_authority(
    db: Session,
    user: User,
    ticket: Ticket,
    *,
    actor_tenant_id: int | None | object = _UNRESOLVED_ACTOR_TENANT,
) -> int | None:
    if actor_tenant_id is _UNRESOLVED_ACTOR_TENANT:
        actor_tenant_id = resolve_actor_tenant_id(db, user)
    assert actor_tenant_id is None or isinstance(actor_tenant_id, int)
    ticket_tenant_id = _relation_tenant_id(ticket)
    relations = (
        ("customer", _ticket_relation(db, ticket, "customer", Customer, "customer_id", relation_kind="customer")),
        ("market", _ticket_relation(db, ticket, "market", Market, "market_id", relation_kind="market")),
        ("team", _ticket_relation(db, ticket, "team", Team, "team_id", relation_kind="team")),
        ("channel_account", _ticket_relation(db, ticket, "channel_account", ChannelAccount, "channel_account_id", relation_kind="channel_account")),
        ("assignee", _ticket_relation(db, ticket, "assignee", User, "assignee_id", relation_kind="assignee")),
        ("creator", _ticket_relation(db, ticket, "creator", User, "created_by", relation_kind="creator")),
    )
    observed = {
        tenant_id
        for _kind, resource in relations
        if resource is not None
        for tenant_id in [_relation_tenant_id(resource)]
        if tenant_id is not None
    }

    if ticket_tenant_id is None:
        if actor_tenant_id is None and not observed and tenant_runtime_authority_mode() == "shadow":
            return None
        _raise(
            "tenant_resource_missing",
            "Ticket has no authoritative Tenant",
            status_code=status.HTTP_409_CONFLICT,
        )

    if actor_tenant_id is None or actor_tenant_id != ticket_tenant_id:
        _raise(
            "tenant_resource_not_found",
            "Ticket is not visible for the authenticated Tenant",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    _active_tenant(db, ticket_tenant_id, principal=False)

    for kind, resource in relations:
        if resource is None:
            continue
        relation_tenant_id = _relation_tenant_id(resource)
        if relation_tenant_id is None or relation_tenant_id != ticket_tenant_id:
            _raise(
                "tenant_resource_conflict",
                f"Ticket {kind} Tenant ownership conflicts",
                status_code=status.HTTP_409_CONFLICT,
            )
    return ticket_tenant_id


def ensure_team_tenant(db: Session, actor_tenant_id: int | None, team: Team) -> int | None:
    tenant_id = ensure_resource_tenant(db, actor_tenant_id, team, resource_kind="Team")
    if team.market_id is not None:
        market = db.query(Market).filter(Market.id == team.market_id).first()
        if market is None:
            _raise(
                "tenant_resource_conflict",
                "Team Market relationship is missing",
                status_code=status.HTTP_409_CONFLICT,
            )
        ensure_resource_tenant(db, actor_tenant_id, market, resource_kind="Market")
        if tenant_id is not None and market.tenant_id != tenant_id:
            _raise(
                "tenant_resource_conflict",
                "Team and Market Tenant ownership conflicts",
                status_code=status.HTTP_409_CONFLICT,
            )
    return tenant_id


def ensure_user_tenant(db: Session, actor_tenant_id: int | None, user: User) -> int | None:
    tenant_id = ensure_resource_tenant(db, actor_tenant_id, user, resource_kind="User")
    if user.team_id is not None:
        team = db.query(Team).filter(Team.id == user.team_id).first()
        if team is None:
            _raise(
                "tenant_resource_conflict",
                "User Team relationship is missing",
                status_code=status.HTTP_409_CONFLICT,
            )
        ensure_team_tenant(db, actor_tenant_id, team)
        if tenant_id is not None and team.tenant_id != tenant_id:
            _raise(
                "tenant_resource_conflict",
                "User and Team Tenant ownership conflicts",
                status_code=status.HTTP_409_CONFLICT,
            )
    return tenant_id


def stamp_runtime_tenant(resource: Any, tenant_id: int | None) -> None:
    if tenant_id is None:
        return
    resource.tenant_id = tenant_id
    resource.tenant_assignment_source = RUNTIME_TENANT_ASSIGNMENT_SOURCE
    resource.tenant_assignment_version = RUNTIME_TENANT_ASSIGNMENT_VERSION


__all__ = [
    "RUNTIME_TENANT_ASSIGNMENT_SOURCE",
    "RUNTIME_TENANT_ASSIGNMENT_VERSION",
    "ensure_resource_tenant",
    "ensure_team_tenant",
    "ensure_ticket_tenant_authority",
    "ensure_user_tenant",
    "resolve_actor_tenant_id",
    "stamp_runtime_tenant",
    "tenant_runtime_authority_mode",
]
