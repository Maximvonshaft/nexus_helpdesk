from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import OutboundEmailAccount
from ..models_outbound_email_tenant import OutboundEmailAccountTenantBinding
from ..services.identity_tenant_scope import active_market_for_actor, actor_tenant_id
from ..services.outbound_email_tenant_context import (
    current_outbound_email_tenant,
    outbound_email_tenant_scope,
)
from ..services.permissions import ensure_can_manage_channel_accounts
from .deps import get_current_user

_PREFIX = "/api/admin/outbound-email"
_ACCOUNT_PATH = re.compile(r"^/api/admin/outbound-email/accounts/(?P<account_id>\d+)(?:/.*)?$")


def _tenant_condition(tenant_id: int | None):
    if tenant_id is None:
        return OutboundEmailAccountTenantBinding.tenant_id.is_(None)
    return OutboundEmailAccountTenantBinding.tenant_id == tenant_id


@event.listens_for(Session, "do_orm_execute")
def _scope_outbound_email_account_selects(execute_state) -> None:  # noqa: ANN001
    scoped, tenant_id = current_outbound_email_tenant()
    if not scoped or not execute_state.is_select:
        return
    descriptions = getattr(execute_state.statement, "column_descriptions", ())
    if not any(item.get("entity") is OutboundEmailAccount for item in descriptions):
        return
    visible_account_ids = select(OutboundEmailAccountTenantBinding.account_id).where(
        _tenant_condition(tenant_id)
    )
    execute_state.statement = execute_state.statement.where(
        OutboundEmailAccount.id.in_(visible_account_ids)
    )


async def _payload(request: Request) -> dict[str, Any]:
    if request.method.upper() not in {"POST", "PUT", "PATCH"}:
        return {}
    try:
        value = await request.json()
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _binding_visible(db: Session, account_id: int, tenant_id: int | None) -> bool:
    query = db.query(OutboundEmailAccountTenantBinding).filter(
        OutboundEmailAccountTenantBinding.account_id == account_id,
    )
    query = query.filter(_tenant_condition(tenant_id))
    return query.first() is not None


async def enforce_outbound_email_tenant_policy(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> AsyncIterator[None]:
    """Apply one server-derived tenant authority to existing email endpoints."""

    if not request.url.path.startswith(_PREFIX):
        yield
        return

    ensure_can_manage_channel_accounts(current_user, db)
    tenant_id = actor_tenant_id(db, current_user)
    payload = await _payload(request)

    if "market_id" in payload and payload["market_id"] is not None:
        try:
            market_id = int(payload["market_id"])
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid market_id",
            ) from exc
        if active_market_for_actor(db, tenant_id, market_id) is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Market not found or inactive",
            )

    match = _ACCOUNT_PATH.fullmatch(request.url.path)
    if match is not None:
        account_id = int(match.group("account_id"))
        if not _binding_visible(db, account_id, tenant_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Outbound Email account not found",
            )

    with outbound_email_tenant_scope(tenant_id):
        yield
