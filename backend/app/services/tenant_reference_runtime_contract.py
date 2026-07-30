from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session, with_loader_criteria

from ..models import IntegrationClient, Market, Team
from ..models_integration_scope import IntegrationClientScope

_INSTALLED = False


def _same_scope(column, tenant_id: int | None):
    return column == tenant_id if tenant_id is not None else column.is_(None)


def _duplicate_reference(
    session: Session,
    *,
    model,
    tenant_id: int | None,
    field_name: str,
    value: str,
    row_id: int | None,
) -> bool:
    column = getattr(model, field_name)
    query = session.query(model.id).filter(
        _same_scope(model.tenant_id, tenant_id),
        func.lower(column) == value.strip().lower(),
    )
    if row_id is not None:
        query = query.filter(model.id != row_id)
    return query.first() is not None


def _enforce_reference_uniqueness(
    session: Session,
    _flush_context,
    _instances,
) -> None:
    candidates = tuple(session.new) + tuple(session.dirty)
    if not candidates:
        return
    with session.no_autoflush:
        for row in candidates:
            if isinstance(row, Team):
                if _duplicate_reference(
                    session,
                    model=Team,
                    tenant_id=row.tenant_id,
                    field_name="name",
                    value=row.name,
                    row_id=row.id,
                ):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Team name already exists in this Tenant",
                    )
            elif isinstance(row, Market):
                for field_name, label in (("code", "code"), ("name", "name")):
                    if _duplicate_reference(
                        session,
                        model=Market,
                        tenant_id=row.tenant_id,
                        field_name=field_name,
                        value=getattr(row, field_name),
                        row_id=row.id,
                    ):
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Market {label} already exists in this Tenant",
                        )


def _scope_admin_integration_clients(execute_state) -> None:  # noqa: ANN001
    if not execute_state.is_select:
        return
    from ..api.admin_tenant_query_scope import _ADMIN_TENANT_ID, _UNSET

    tenant_id = _ADMIN_TENANT_ID.get()
    if tenant_id is _UNSET:
        return
    if tenant_id is None:
        client_ids = select(IntegrationClientScope.client_id).where(
            IntegrationClientScope.scope_type == "platform",
            IntegrationClientScope.tenant_id.is_(None),
        )
        scope_predicate = (
            IntegrationClientScope.scope_type == "platform"
        ) & IntegrationClientScope.tenant_id.is_(None)
    else:
        client_ids = select(IntegrationClientScope.client_id).where(
            IntegrationClientScope.scope_type == "tenant",
            IntegrationClientScope.tenant_id == int(tenant_id),
        )
        scope_predicate = (
            IntegrationClientScope.scope_type == "tenant"
        ) & (IntegrationClientScope.tenant_id == int(tenant_id))
    execute_state.statement = execute_state.statement.options(
        with_loader_criteria(
            IntegrationClient,
            IntegrationClient.id.in_(client_ids),
            include_aliases=True,
        ),
        with_loader_criteria(
            IntegrationClientScope,
            scope_predicate,
            include_aliases=True,
        ),
    )


def _disable_global_team_precheck(*_args, **_kwargs) -> None:
    """The row-owned Tenant guard and database index are the sole contract."""


def install_tenant_reference_runtime_contract() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from ..api import admin_identity

    event.listen(Session, "before_flush", _enforce_reference_uniqueness)
    event.listen(Session, "do_orm_execute", _scope_admin_integration_clients)
    admin_identity._ensure_unique_team_name = _disable_global_team_precheck
    _INSTALLED = True


__all__ = ["install_tenant_reference_runtime_contract"]
