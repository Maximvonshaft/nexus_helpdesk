"""Bind Integration principals to Tenant authority and fix reference uniqueness.

Revision ID: 20260729_r15_tenant_scope
Revises: 20260729_wa5_signup_checkpoint
Create Date: 2026-07-29
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "20260729_r15_tenant_scope"
down_revision = "20260729_wa5_signup_checkpoint"
branch_labels = None
depends_on = None


def _unique_constraints(bind, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    return {
        str(item["name"])
        for item in inspector.get_unique_constraints(table_name)
        if item.get("name")
    }


def _indexes(bind, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    return {
        str(item["name"])
        for item in inspector.get_indexes(table_name)
        if item.get("name")
    }


def _drop_unique_constraint_if_present(bind, table_name: str, name: str) -> None:
    if name not in _unique_constraints(bind, table_name):
        return
    with op.batch_alter_table(table_name) as batch:
        batch.drop_constraint(name, type_="unique")


def _drop_index_if_present(bind, table_name: str, name: str) -> None:
    if name in _indexes(bind, table_name):
        op.drop_index(name, table_name=table_name)


def _create_partial_lower_unique(
    name: str,
    table_name: str,
    columns: list,
    predicate: str,
) -> None:
    op.create_index(
        name,
        table_name,
        columns,
        unique=True,
        postgresql_where=sa.text(predicate),
        sqlite_where=sa.text(predicate),
    )


def _duplicate_exists(bind, statement: str) -> bool:
    return bind.execute(sa.text(statement)).first() is not None


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "integration_client_scopes",
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column("scope_type", sa.String(length=24), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=True),
        sa.Column("assigned_by", sa.Integer(), nullable=True),
        sa.Column(
            "assignment_source",
            sa.String(length=80),
            nullable=False,
            server_default="explicit_admin_assignment",
        ),
        sa.Column(
            "assignment_version",
            sa.String(length=80),
            nullable=False,
            server_default="nexus.integration-principal-scope.v1",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "scope_type IN ('tenant','platform')",
            name="ck_integration_client_scope_type",
        ),
        sa.CheckConstraint(
            "(scope_type = 'tenant' AND tenant_id IS NOT NULL) OR "
            "(scope_type = 'platform' AND tenant_id IS NULL)",
            name="ck_integration_client_scope_ownership",
        ),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["integration_clients.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("client_id"),
    )
    op.create_index(
        "ix_integration_client_scopes_tenant_id",
        "integration_client_scopes",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_integration_client_scopes_assigned_by",
        "integration_client_scopes",
        ["assigned_by"],
        unique=False,
    )
    op.create_index(
        "ix_integration_client_scopes_tenant_type",
        "integration_client_scopes",
        ["tenant_id", "scope_type"],
        unique=False,
    )

    # GET profile responses never require idempotent business-payload replay.
    # Remove historical customer identity and Case-history copies in place.
    redacted = json.dumps(
        {
            "schema": "nexus.integration-log.redacted.v1",
            "redacted": True,
            "reason": "profile_response_pii_removed",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    bind.execute(
        sa.text(
            "UPDATE integration_request_logs "
            "SET response_json = :redacted "
            "WHERE endpoint = 'integration.profile' AND response_json IS NOT NULL"
        ),
        {"redacted": redacted},
    )

    _drop_index_if_present(bind, "teams", "ix_teams_name")
    _drop_unique_constraint_if_present(bind, "teams", "uq_teams_name")
    _drop_index_if_present(bind, "markets", "ix_markets_code")
    _drop_index_if_present(bind, "markets", "ix_markets_name")
    _drop_unique_constraint_if_present(bind, "markets", "uq_markets_code")
    _drop_unique_constraint_if_present(bind, "markets", "uq_markets_name")

    _create_partial_lower_unique(
        "uq_teams_tenant_lower_name",
        "teams",
        ["tenant_id", sa.text("lower(name)")],
        "tenant_id IS NOT NULL",
    )
    _create_partial_lower_unique(
        "uq_teams_shadow_lower_name",
        "teams",
        [sa.text("lower(name)")],
        "tenant_id IS NULL",
    )
    _create_partial_lower_unique(
        "uq_markets_tenant_lower_code",
        "markets",
        ["tenant_id", sa.text("lower(code)")],
        "tenant_id IS NOT NULL",
    )
    _create_partial_lower_unique(
        "uq_markets_shadow_lower_code",
        "markets",
        [sa.text("lower(code)")],
        "tenant_id IS NULL",
    )
    _create_partial_lower_unique(
        "uq_markets_tenant_lower_name",
        "markets",
        ["tenant_id", sa.text("lower(name)")],
        "tenant_id IS NOT NULL",
    )
    _create_partial_lower_unique(
        "uq_markets_shadow_lower_name",
        "markets",
        [sa.text("lower(name)")],
        "tenant_id IS NULL",
    )


def downgrade() -> None:
    bind = op.get_bind()

    conflicts = {
        "teams.name": (
            "SELECT lower(name) FROM teams GROUP BY lower(name) HAVING count(*) > 1 LIMIT 1"
        ),
        "markets.code": (
            "SELECT lower(code) FROM markets GROUP BY lower(code) HAVING count(*) > 1 LIMIT 1"
        ),
        "markets.name": (
            "SELECT lower(name) FROM markets GROUP BY lower(name) HAVING count(*) > 1 LIMIT 1"
        ),
    }
    blocked = [key for key, sql in conflicts.items() if _duplicate_exists(bind, sql)]
    if blocked:
        raise RuntimeError(
            "r15_tenant_reference_uniqueness_downgrade_blocked:" + ",".join(blocked)
        )

    for table_name, name in (
        ("teams", "uq_teams_tenant_lower_name"),
        ("teams", "uq_teams_shadow_lower_name"),
        ("markets", "uq_markets_tenant_lower_code"),
        ("markets", "uq_markets_shadow_lower_code"),
        ("markets", "uq_markets_tenant_lower_name"),
        ("markets", "uq_markets_shadow_lower_name"),
    ):
        _drop_index_if_present(bind, table_name, name)

    with op.batch_alter_table("teams") as batch:
        batch.create_unique_constraint("uq_teams_name", ["name"])
    with op.batch_alter_table("markets") as batch:
        batch.create_unique_constraint("uq_markets_code", ["code"])
        batch.create_unique_constraint("uq_markets_name", ["name"])
    op.create_index("ix_teams_name", "teams", ["name"], unique=True)
    op.create_index("ix_markets_code", "markets", ["code"], unique=True)
    op.create_index("ix_markets_name", "markets", ["name"], unique=True)

    op.drop_table("integration_client_scopes")
