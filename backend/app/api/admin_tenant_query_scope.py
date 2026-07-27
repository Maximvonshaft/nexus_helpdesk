from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextvars import ContextVar, Token
from typing import Any

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import event, false, or_, select
from sqlalchemy.orm import Session, with_loader_criteria

from ..db import get_db
from ..models import (
    AIConfigResource,
    AIConfigVersion,
    AdminAuditLog,
    BackgroundJob,
    ChannelAccount,
    Market,
    MarketBulletin,
    OutboundEmailAccount,
    Team,
    Ticket,
    TicketOutboundMessage,
    User,
    UserCapabilityOverride,
)
from ..models_job_scope import BackgroundJobScope
from ..operator_models import OperatorQueueScopeGrant, OperatorTask
from ..services.identity_tenant_scope import active_market_for_actor, actor_tenant_id
from ..services.tenant_authority import (
    RUNTIME_TENANT_ASSIGNMENT_SOURCE,
    RUNTIME_TENANT_ASSIGNMENT_VERSION,
    tenant_key_for_id,
)
from ..webchat_models import WebchatConversation, WebchatHandoffRequest
from .deps import get_current_user

_UNSET = object()
_ADMIN_TENANT_ID: ContextVar[int | None | object] = ContextVar(
    "nexus_admin_tenant_query_scope",
    default=_UNSET,
)
_ADMIN_TENANT_KEY: ContextVar[str | None | object] = ContextVar(
    "nexus_admin_tenant_key_scope",
    default=_UNSET,
)
_EMAIL_ACCOUNT_COLLECTION = "/api/admin/outbound-email/accounts"
_EMAIL_ACCOUNT_TARGET = re.compile(r"^/api/admin/outbound-email/accounts/\d+(?:/.*)?$")


def _tenant_expression(column, tenant_id: int | None):
    return column == tenant_id if tenant_id is not None else column.is_(None)


def _scope_error(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "error_code": "admin_tenant_write_scope_conflict",
            "message": message,
        },
    )


def _tenant_key_scope(tenant_key: str | None, column):
    return column == tenant_key if tenant_key is not None else false()


@event.listens_for(Session, "do_orm_execute")
def _apply_admin_tenant_criteria(execute_state) -> None:  # noqa: ANN001
    tenant_id = _ADMIN_TENANT_ID.get()
    tenant_key = _ADMIN_TENANT_KEY.get()
    if (
        tenant_id is _UNSET
        or tenant_key is _UNSET
        or not execute_state.is_select
    ):
        return
    assert tenant_id is None or isinstance(tenant_id, int)
    assert tenant_key is None or isinstance(tenant_key, str)

    user_ids = select(User.id).where(_tenant_expression(User.tenant_id, tenant_id))
    market_ids = select(Market.id).where(_tenant_expression(Market.tenant_id, tenant_id))
    ticket_ids = select(Ticket.id).where(_tenant_expression(Ticket.tenant_id, tenant_id))
    conversation_ids = select(WebchatConversation.id).where(
        _tenant_key_scope(tenant_key, WebchatConversation.tenant_key)
    )
    job_ids = select(BackgroundJobScope.job_id).where(
        BackgroundJobScope.scope_type == "tenant",
        _tenant_expression(BackgroundJobScope.tenant_id, tenant_id),
    )

    email_account_scope = OutboundEmailAccount.market_id.in_(market_ids)
    channel_account_scope = _tenant_expression(ChannelAccount.tenant_id, tenant_id)
    bulletin_scope = MarketBulletin.market_id.in_(market_ids)
    ai_config_scope = AIConfigResource.market_id.in_(market_ids)
    if tenant_id is None:
        email_account_scope = or_(
            OutboundEmailAccount.market_id.is_(None),
            email_account_scope,
        )
        bulletin_scope = or_(MarketBulletin.market_id.is_(None), bulletin_scope)
        ai_config_scope = or_(AIConfigResource.market_id.is_(None), ai_config_scope)

    operator_task_scope = or_(
        OperatorTask.ticket_id.in_(ticket_ids),
        OperatorTask.webchat_conversation_id.in_(conversation_ids),
    )
    handoff_scope = WebchatHandoffRequest.conversation_id.in_(conversation_ids)
    ai_config_ids = select(AIConfigResource.id).where(ai_config_scope)

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
            Ticket,
            _tenant_expression(Ticket.tenant_id, tenant_id),
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
        with_loader_criteria(
            MarketBulletin,
            bulletin_scope,
            include_aliases=True,
        ),
        with_loader_criteria(
            AIConfigResource,
            ai_config_scope,
            include_aliases=True,
        ),
        with_loader_criteria(
            AIConfigVersion,
            AIConfigVersion.resource_id.in_(ai_config_ids),
            include_aliases=True,
        ),
        with_loader_criteria(
            WebchatConversation,
            WebchatConversation.id.in_(conversation_ids),
            include_aliases=True,
        ),
        with_loader_criteria(
            WebchatHandoffRequest,
            handoff_scope,
            include_aliases=True,
        ),
        with_loader_criteria(
            OperatorTask,
            operator_task_scope,
            include_aliases=True,
        ),
        with_loader_criteria(
            OperatorQueueScopeGrant,
            _tenant_key_scope(tenant_key, OperatorQueueScopeGrant.tenant_key),
            include_aliases=True,
        ),
        with_loader_criteria(
            BackgroundJobScope,
            _tenant_expression(BackgroundJobScope.tenant_id, tenant_id),
            include_aliases=True,
        ),
        with_loader_criteria(
            BackgroundJob,
            BackgroundJob.id.in_(job_ids),
            include_aliases=True,
        ),
        with_loader_criteria(
            TicketOutboundMessage,
            TicketOutboundMessage.ticket_id.in_(ticket_ids),
            include_aliases=True,
        ),
    )


def _stamp_tenant_owned(resource: Any, tenant_id: int | None) -> None:
    observed = getattr(resource, "tenant_id", None)
    if observed is not None and observed != tenant_id:
        raise _scope_error(
            f"{type(resource).__name__} Tenant ownership conflicts with the authenticated principal"
        )
    if tenant_id is None:
        if observed is not None:
            raise _scope_error(
                f"{type(resource).__name__} cannot be written outside its Tenant"
            )
        return
    resource.tenant_id = tenant_id
    if hasattr(resource, "tenant_assignment_source"):
        resource.tenant_assignment_source = RUNTIME_TENANT_ASSIGNMENT_SOURCE
    if hasattr(resource, "tenant_assignment_version"):
        resource.tenant_assignment_version = RUNTIME_TENANT_ASSIGNMENT_VERSION


def _require_market(
    session: Session,
    *,
    tenant_id: int | None,
    market_id: int | None,
    resource_name: str,
    required: bool = False,
) -> Market | None:
    if market_id is None:
        if required and tenant_id is not None:
            raise _scope_error(
                f"{resource_name} requires a Tenant-owned Market"
            )
        return None
    market = session.get(Market, int(market_id))
    if market is None or market.tenant_id != tenant_id or not market.is_active:
        raise _scope_error(
            f"{resource_name} Market is missing, inactive or outside the authenticated Tenant"
        )
    return market


def _validate_admin_write(
    session: Session,
    resource: Any,
    tenant_id: int | None,
) -> None:
    if isinstance(resource, (User, Team, Market, ChannelAccount)):
        _stamp_tenant_owned(resource, tenant_id)

    if isinstance(resource, User) and resource.team_id is not None:
        team = session.get(Team, int(resource.team_id))
        if team is None or team.tenant_id != tenant_id:
            raise _scope_error("User Team is outside the authenticated Tenant")

    if isinstance(resource, Team):
        _require_market(
            session,
            tenant_id=tenant_id,
            market_id=resource.market_id,
            resource_name="Team",
        )

    if isinstance(resource, ChannelAccount):
        _require_market(
            session,
            tenant_id=tenant_id,
            market_id=resource.market_id,
            resource_name="ChannelAccount",
        )
        fallback_id = str(resource.fallback_account_id or "").strip()
        if fallback_id:
            fallback = (
                session.query(ChannelAccount)
                .filter(ChannelAccount.account_id == fallback_id)
                .first()
            )
            if fallback is None or fallback.tenant_id != tenant_id:
                raise _scope_error(
                    "ChannelAccount fallback is outside the authenticated Tenant"
                )

    if isinstance(resource, MarketBulletin):
        _require_market(
            session,
            tenant_id=tenant_id,
            market_id=resource.market_id,
            resource_name="MarketBulletin",
            required=True,
        )

    if isinstance(resource, AIConfigResource):
        _require_market(
            session,
            tenant_id=tenant_id,
            market_id=resource.market_id,
            resource_name="AIConfigResource",
            required=True,
        )


@event.listens_for(Session, "before_flush")
def _enforce_admin_tenant_writes(
    session: Session,
    _flush_context,
    _instances,
) -> None:
    tenant_id = _ADMIN_TENANT_ID.get()
    if tenant_id is _UNSET:
        return
    assert tenant_id is None or isinstance(tenant_id, int)
    candidates = tuple(session.new) + tuple(session.dirty)
    if not candidates:
        return
    with session.no_autoflush:
        for resource in candidates:
            _validate_admin_write(session, resource, tenant_id)


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


def _set_scope(
    tenant_id: int | None,
    tenant_key: str | None,
) -> tuple[Token, Token]:
    return (
        _ADMIN_TENANT_ID.set(tenant_id),
        _ADMIN_TENANT_KEY.set(tenant_key),
    )


async def enforce_admin_tenant_query_scope(
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> AsyncIterator[None]:
    """Apply one server-derived Tenant boundary to canonical admin surfaces.

    The dependency scopes reads and activates the corresponding write invariant.
    Endpoint-specific capabilities, validation, mutation and audit remain in their
    existing authorities.
    """

    tenant_id = actor_tenant_id(db, current_user)
    tenant_key = tenant_key_for_id(db, tenant_id) if tenant_id is not None else None
    if _is_email_account_write(request):
        payload = await _payload(request)
        market_present = "market_id" in payload
        market_id = payload.get("market_id") if market_present else None
        creating_account = (
            request.method.upper() == "POST"
            and request.url.path == _EMAIL_ACCOUNT_COLLECTION
        )
        if creating_account and tenant_id is not None and not market_present:
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

    tenant_id_token, tenant_key_token = _set_scope(tenant_id, tenant_key)
    try:
        yield
    finally:
        _ADMIN_TENANT_KEY.reset(tenant_key_token)
        _ADMIN_TENANT_ID.reset(tenant_id_token)
