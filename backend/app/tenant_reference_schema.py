from __future__ import annotations

from sqlalchemy import Index, UniqueConstraint, func

from .models import Market, Team

_INSTALLED = False


def _remove_single_column_unique(table, column_name: str) -> None:  # noqa: ANN001
    for constraint in list(table.constraints):
        if not isinstance(constraint, UniqueConstraint):
            continue
        columns = [column.name for column in constraint.columns]
        if columns == [column_name]:
            table.constraints.remove(constraint)


def install_tenant_reference_schema() -> None:
    """Replace single-Tenant uniqueness with explicit Tenant-scoped identity.

    The production database is changed by the matching Alembic revision. This
    metadata normalizer keeps tests, drift checks and fresh local databases on
    exactly the same contract without editing the historical baseline models.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    _remove_single_column_unique(Team.__table__, "name")
    _remove_single_column_unique(Market.__table__, "code")
    _remove_single_column_unique(Market.__table__, "name")

    Index(
        "uq_teams_tenant_name_ci",
        Team.tenant_id,
        func.lower(Team.name),
        unique=True,
        postgresql_where=Team.tenant_id.is_not(None),
        sqlite_where=Team.tenant_id.is_not(None),
    )
    Index(
        "uq_teams_shadow_name_ci",
        func.lower(Team.name),
        unique=True,
        postgresql_where=Team.tenant_id.is_(None),
        sqlite_where=Team.tenant_id.is_(None),
    )
    Index(
        "uq_markets_tenant_code_ci",
        Market.tenant_id,
        func.lower(Market.code),
        unique=True,
        postgresql_where=Market.tenant_id.is_not(None),
        sqlite_where=Market.tenant_id.is_not(None),
    )
    Index(
        "uq_markets_shadow_code_ci",
        func.lower(Market.code),
        unique=True,
        postgresql_where=Market.tenant_id.is_(None),
        sqlite_where=Market.tenant_id.is_(None),
    )
    Index(
        "uq_markets_tenant_name_ci",
        Market.tenant_id,
        func.lower(Market.name),
        unique=True,
        postgresql_where=Market.tenant_id.is_not(None),
        sqlite_where=Market.tenant_id.is_not(None),
    )
    Index(
        "uq_markets_shadow_name_ci",
        func.lower(Market.name),
        unique=True,
        postgresql_where=Market.tenant_id.is_(None),
        sqlite_where=Market.tenant_id.is_(None),
    )
    _INSTALLED = True


__all__ = ["install_tenant_reference_schema"]
