from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextvars import ContextVar, Token
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import event, or_, select
from sqlalchemy.orm import Session, with_loader_criteria

from ..db import get_db
from ..models import (
    AdminAuditLog,
    ChannelAccount,
    Market,
    OutboundEmailAccount,
    Team,
    User,
    UserCapabilityOverride,
)
from ..services.identity_tenant_scope import active_market_for_actor, actor_tenant_id
from .deps import get_current_user

_UNSET = object()
_ADMIN_TENANT_ID: ContextVar[int | None | object] = ContextVar(
    "nexus_admin_tenant_query_scope",
    default=_UNSET,
)
_EMAIL_ACCOUNT_COLLECTION = "/api/admin/outbound-email/accounts"
_EMAIL_ACCOUNT_TARGET = re.compile(r"^/api/admin/outbound-email/accounts/\d+(?:/.*)?$")


def _tenant_expression(column, tenant_id: int | None):
    return column == tenant_id if tenant_id is not None else column.is_(None)


@event.listens_for(Session, "do_orm_execute")
def _apply_admin_tenant_criteria(execute_state) -> None:  # noqa: ANN001
    tenant_id = _ADMIN_TENANT_ID.get()
    if tenant_id is _UNSET or not execute_state.is_select:
        return
    assert tenant_id is None or isinstance(tenant_id, int)

    user_ids = select(User.id).where(_tenant_expression(User.tenant_id, tenant_id))
    market_ids = select(Market.id).where(_tenant_expression(Market.tenant_id, tenant_id))
    email_account_scope = OutboundEmailAccount.market_id.in_(market_ids)
    channel_account_scope = ChannelAccount.market_id.in_(market_ids)
    if tenant_id is None:
        email_account_scope = or_(OutboundEmailAccount.market_id.is_(None), email_account_scope)
        channel_account_scope = or_(ChannelAccount.market_id.is_(None), channel_account_scope)

    execute_state.statement = execute_state.statement.options(
        with_loader_criteria(
            User,
            _tenant_expression(User.tenant_id, tenant_id),
            include_aliases=True,
        ),
        with_loader_criteria(
            Team,
            _tenant_expression(Team.tenant_id, tenant_id),
            include_aliases=True,
        ),
        with_loader_criteria(
            Market,
            _tenant_expression(Market.tenant_id, tenant_id),
            include_aliases=True,
        ),
        with_loader_criteria(
            UserCapabilityOverride,
            UserCapabilityOverride.user_id.in_(user_ids),
            include_aliases=True,
        ),
        with_loader_criteria(
            AdminAuditLog,
            AdminAuditLog.actor_id.in_(user_ids),
            include_aliases=True,
        ),
        with_loader_criteria(
            OutboundEmailAccount,
            email_account_scope,
            include_aliases=True,
        ),
        with_loader_criteria(
            ChannelAccount,
            channel_account_scope,
            include_aliases=True,
        ),
    )


async def _payload(request: Request) -> dict[str, Any]:
    if request.method.upper() not in {"POST", "PUT", "PATCH"}:
        return {}
    try:
        value = await request.json()
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _is_email_account_write(request: Request) -> bool:
    if request.method.upper() not in {"POST", "PUT", "PATCH"}:
        return False
    return request.url.path == _EMAIL_ACCOUNT_COLLECTION or bool(
        _EMAIL_ACCOUNT_TARGET.fullmatch(request.url.path)
    )


def _set_scope(tenant_id: int | None) -> Token:
    return _ADMIN_TENANT_ID.set(tenant_id)


async def enforce_admin_tenant_query_scope(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> AsyncIterator[None]:
    """Apply one server-derived Tenant boundary to the canonical admin router.

    This dependency owns query scoping only. Endpoint-specific capability checks,
    validation, mutation and audit remain in their existing authorities.
    """

    tenant_id = actor_tenant_id(db, current_user)
    if _is_email_account_write(request):
        payload = await _payload(request)
        market_present = "market_id" in payload
        market_id = payload.get("market_id") if market_present else None
        if request.method.upper() == "POST" and tenant_id is not None and not market_present:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="market_id is required for a tenant-bound email account",
            )
        if market_present:
            if market_id is None:
                if tenant_id is not None:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="market_id is required for a tenant-bound email account",
                    )
            else:
                try:
                    normalized_market_id = int(market_id)
                except (TypeError, ValueError) as exc:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Invalid market_id",
                    ) from exc
                if active_market_for_actor(db, tenant_id, normalized_market_id) is None:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Market not found or inactive",
                    )

    token = _set_scope(tenant_id)
    try:
        yield
    finally:
        _ADMIN_TENANT_ID.reset(token)
