"""Replace platform-global Team and Market uniqueness with Tenant identity.

Revision ID: 20260729_r15_tenant_reference
Revises: 20260729_r15_integration_scope
Create Date: 2026-07-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260729_r15_tenant_reference"
down_revision = "20260729_r15_integration_scope"
branch_labels = None
depends_on = None


def _duplicate_rows(bind, table: str, column: str):  # noqa: ANN001
    return bind.execute(
        sa.text(
            f"SELECT tenant_id, lower(trim({column})) AS normalized_value, "
            "count(*) AS row_count "
            f"FROM {table} "
            f"WHERE trim({column}) <> '' "
            "GROUP BY tenant_id, lower(trim(" + column + ")) "
            "HAVING count(*) > 1 "
            "ORDER BY tenant_id, normalized_value"
        )
    ).mappings().all()


def _global_duplicates(bind, table: str, column: str):  # noqa: ANN001
    return bind.execute(
        sa.text(
            f"SELECT lower(trim({column})) AS normalized_value, count(*) AS row_count "
            f"FROM {table} WHERE trim({column}) <> '' "
            f"GROUP BY lower(trim({column})) HAVING count(*) > 1 "
            "ORDER BY normalized_value"
        )
    ).mappings().all()


def _unique_constraint_names(bind, table: str) -> set[str]:  # noqa: ANN001
    return {
        str(item["name"])
        for item in sa.inspect(bind).get_unique_constraints(table)
        if item.get("name")
    }


def _index_names(bind, table: str) -> set[str]:  # noqa: ANN001
    return {
        str(item["name"])
        for item in sa.inspect(bind).get_indexes(table)
        if item.get("name")
    }


def _drop_unique(bind, table: str, name: str) -> None:  # noqa: ANN001
    if name not in _unique_constraint_names(bind, table):
        return
    with op.batch_alter_table(table) as batch:
        batch.drop_constraint(name, type_="unique")


def _drop_index(bind, table: str, name: str) -> None:  # noqa: ANN001
    if name in _index_names(bind, table):
        op.drop_index(name, table_name=table)


def _create_index(
    name: str,
    table: str,
    expressions: list,
    *,
    where,
) -> None:  # noqa: ANN001
    op.create_index(
        name,
        table,
        expressions,
        unique=True,
        postgresql_where=where,
        sqlite_where=where,
    )


def upgrade() -> None:
    bind = op.get_bind()
    conflicts = {
        "teams.name": _duplicate_rows(bind, "teams", "name"),
        "markets.code": _duplicate_rows(bind, "markets", "code"),
        "markets.name": _duplicate_rows(bind, "markets", "name"),
    }
    active_conflicts = {key: rows for key, rows in conflicts.items() if rows}
    if active_conflicts:
        keys = ",".join(sorted(active_conflicts))
        raise RuntimeError(
            "r15_tenant_reference_duplicate_preflight_failed:" + keys
        )

    _drop_index(bind, "teams", "ix_teams_name")
    _drop_unique(bind, "teams", "uq_teams_name")
    _drop_index(bind, "markets", "ix_markets_code")
    _drop_index(bind, "markets", "ix_markets_name")
    _drop_unique(bind, "markets", "uq_markets_code")
    _drop_unique(bind, "markets", "uq_markets_name")

    _create_index(
        "uq_teams_tenant_name_ci",
        "teams",
        [sa.text("tenant_id"), sa.text("lower(name)")],
        where=sa.text("tenant_id IS NOT NULL"),
    )
    _create_index(
        "uq_teams_shadow_name_ci",
        "teams",
        [sa.text("lower(name)")],
        where=sa.text("tenant_id IS NULL"),
    )
    _create_index(
        "uq_markets_tenant_code_ci",
        "markets",
        [sa.text("tenant_id"), sa.text("lower(code)")],
        where=sa.text("tenant_id IS NOT NULL"),
    )
    _create_index(
        "uq_markets_shadow_code_ci",
        "markets",
        [sa.text("lower(code)")],
        where=sa.text("tenant_id IS NULL"),
    )
    _create_index(
        "uq_markets_tenant_name_ci",
        "markets",
        [sa.text("tenant_id"), sa.text("lower(name)")],
        where=sa.text("tenant_id IS NOT NULL"),
    )
    _create_index(
        "uq_markets_shadow_name_ci",
        "markets",
        [sa.text("lower(name)")],
        where=sa.text("tenant_id IS NULL"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    conflicts = {
        "teams.name": _global_duplicates(bind, "teams", "name"),
        "markets.code": _global_duplicates(bind, "markets", "code"),
        "markets.name": _global_duplicates(bind, "markets", "name"),
    }
    active_conflicts = {key: rows for key, rows in conflicts.items() if rows}
    if active_conflicts:
        keys = ",".join(sorted(active_conflicts))
        raise RuntimeError(
            "r15_tenant_reference_downgrade_irreversible_with_cross_tenant_duplicates:"
            + keys
        )

    for table, name in (
        ("teams", "uq_teams_tenant_name_ci"),
        ("teams", "uq_teams_shadow_name_ci"),
        ("markets", "uq_markets_tenant_code_ci"),
        ("markets", "uq_markets_shadow_code_ci"),
        ("markets", "uq_markets_tenant_name_ci"),
        ("markets", "uq_markets_shadow_name_ci"),
    ):
        _drop_index(bind, table, name)

    with op.batch_alter_table("teams") as batch:
        batch.create_unique_constraint("uq_teams_name", ["name"])
    with op.batch_alter_table("markets") as batch:
        batch.create_unique_constraint("uq_markets_code", ["code"])
        batch.create_unique_constraint("uq_markets_name", ["name"])
    op.create_index("ix_markets_code", "markets", ["code"], unique=True)
    op.create_index("ix_markets_name", "markets", ["name"], unique=True)
