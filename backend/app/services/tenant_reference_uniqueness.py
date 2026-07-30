from __future__ import annotations

from sqlalchemy import Index, UniqueConstraint, func, text

from ..models import Market, Team

_INSTALLED = False


def _remove_legacy_unique(table, *, column_names: set[str], index_names: set[str]) -> None:
    for constraint in list(table.constraints):
        if not isinstance(constraint, UniqueConstraint):
            continue
        observed = {column.name for column in constraint.columns}
        if observed == column_names:
            table.constraints.remove(constraint)
    for index in list(table.indexes):
        observed = {column.name for column in index.columns if hasattr(column, "name")}
        if index.unique and (index.name in index_names or observed == column_names):
            table.indexes.remove(index)


def install_tenant_reference_uniqueness_metadata() -> None:
    """Replace single-Tenant-era global uniqueness in SQLAlchemy metadata.

    Alembic owns the live-database migration. This function keeps tests,
    ephemeral databases and drift inspection aligned with the migrated schema.
    """

    global _INSTALLED
    if _INSTALLED:
        return

    _remove_legacy_unique(
        Team.__table__,
        column_names={"name"},
        index_names={"ix_teams_name", "uq_teams_name"},
    )
    _remove_legacy_unique(
        Market.__table__,
        column_names={"code"},
        index_names={"ix_markets_code", "uq_markets_code"},
    )
    _remove_legacy_unique(
        Market.__table__,
        column_names={"name"},
        index_names={"ix_markets_name", "uq_markets_name"},
    )

    indexes = (
        Index(
            "uq_teams_tenant_lower_name",
            Team.tenant_id,
            func.lower(Team.name),
            unique=True,
            postgresql_where=text("tenant_id IS NOT NULL"),
            sqlite_where=text("tenant_id IS NOT NULL"),
        ),
        Index(
            "uq_teams_shadow_lower_name",
            func.lower(Team.name),
            unique=True,
            postgresql_where=text("tenant_id IS NULL"),
            sqlite_where=text("tenant_id IS NULL"),
        ),
        Index(
            "uq_markets_tenant_lower_code",
            Market.tenant_id,
            func.lower(Market.code),
            unique=True,
            postgresql_where=text("tenant_id IS NOT NULL"),
            sqlite_where=text("tenant_id IS NOT NULL"),
        ),
        Index(
            "uq_markets_shadow_lower_code",
            func.lower(Market.code),
            unique=True,
            postgresql_where=text("tenant_id IS NULL"),
            sqlite_where=text("tenant_id IS NULL"),
        ),
        Index(
            "uq_markets_tenant_lower_name",
            Market.tenant_id,
            func.lower(Market.name),
            unique=True,
            postgresql_where=text("tenant_id IS NOT NULL"),
            sqlite_where=text("tenant_id IS NOT NULL"),
        ),
        Index(
            "uq_markets_shadow_lower_name",
            func.lower(Market.name),
            unique=True,
            postgresql_where=text("tenant_id IS NULL"),
            sqlite_where=text("tenant_id IS NULL"),
        ),
    )
    for index in indexes:
        table = Team.__table__ if index.name.startswith("uq_teams_") else Market.__table__
        if index.name not in {item.name for item in table.indexes}:
            table.append_constraint(index)

    _INSTALLED = True


__all__ = ["install_tenant_reference_uniqueness_metadata"]
