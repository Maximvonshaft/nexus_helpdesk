"""add authoritative Tenant principal and nullable core ownership links

Revision ID: 20260713_0059
Revises: 20260711_0058
Create Date: 2026-07-13

This revision is deliberately additive. It creates no Tenant rows, performs no
historical assignment, changes no authorization path, and leaves every new core
ownership column nullable until an approved mapping/backfill phase is complete.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260713_0059"
down_revision = "20260711_0058"
branch_labels = None
depends_on = None

_TENANT_TABLE = "tenants"
_CORE_TABLES = (
    "markets",
    "teams",
    "users",
    "channel_accounts",
    "customers",
    "tickets",
)


def _recreate_mode(bind) -> str:
    return "always" if bind.dialect.name == "sqlite" else "auto"


def _fk_name(table_name: str) -> str:
    return f"fk_{table_name}_tenant_id_tenants"


def _index_name(table_name: str) -> str:
    return f"ix_{table_name}_tenant_id"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TENANT_TABLE not in inspector.get_table_names():
        op.create_table(
            _TENANT_TABLE,
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("tenant_key", sa.String(length=120), nullable=False),
            sa.Column("display_name", sa.String(length=160), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.CheckConstraint(
                "length(trim(tenant_key)) > 0",
                name="ck_tenants_key_nonempty",
            ),
            sa.CheckConstraint(
                "tenant_key = lower(tenant_key)",
                name="ck_tenants_key_lowercase",
            ),
            sa.CheckConstraint(
                "tenant_key <> 'default'",
                name="ck_tenants_key_not_default",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("tenant_key", name="uq_tenants_tenant_key"),
        )
        op.create_index("ix_tenants_is_active", _TENANT_TABLE, ["is_active"])

    for table_name in _CORE_TABLES:
        inspector = sa.inspect(bind)
        if table_name not in inspector.get_table_names():
            raise RuntimeError(f"required core table missing: {table_name}")
        columns = {item["name"] for item in inspector.get_columns(table_name)}
        indexes = {item["name"] for item in inspector.get_indexes(table_name)}
        foreign_keys = {item.get("name") for item in inspector.get_foreign_keys(table_name)}
        needs_change = bool(
            {"tenant_id", "tenant_assignment_source", "tenant_assignment_version"} - columns
            or _index_name(table_name) not in indexes
            or _fk_name(table_name) not in foreign_keys
        )
        if not needs_change:
            continue
        with op.batch_alter_table(
            table_name,
            recreate=_recreate_mode(bind),
        ) as batch:
            if "tenant_id" not in columns:
                batch.add_column(sa.Column("tenant_id", sa.Integer(), nullable=True))
            if "tenant_assignment_source" not in columns:
                batch.add_column(
                    sa.Column("tenant_assignment_source", sa.String(length=40), nullable=True)
                )
            if "tenant_assignment_version" not in columns:
                batch.add_column(
                    sa.Column("tenant_assignment_version", sa.String(length=80), nullable=True)
                )
            if _fk_name(table_name) not in foreign_keys:
                batch.create_foreign_key(
                    _fk_name(table_name),
                    _TENANT_TABLE,
                    ["tenant_id"],
                    ["id"],
                    ondelete="RESTRICT",
                )
            if _index_name(table_name) not in indexes:
                batch.create_index(_index_name(table_name), ["tenant_id"])


def downgrade() -> None:
    bind = op.get_bind()
    for table_name in reversed(_CORE_TABLES):
        inspector = sa.inspect(bind)
        if table_name not in inspector.get_table_names():
            continue
        columns = {item["name"] for item in inspector.get_columns(table_name)}
        indexes = {item["name"] for item in inspector.get_indexes(table_name)}
        foreign_keys = {item.get("name") for item in inspector.get_foreign_keys(table_name)}
        if not (
            columns & {"tenant_id", "tenant_assignment_source", "tenant_assignment_version"}
            or _index_name(table_name) in indexes
            or _fk_name(table_name) in foreign_keys
        ):
            continue
        with op.batch_alter_table(
            table_name,
            recreate=_recreate_mode(bind),
        ) as batch:
            if _index_name(table_name) in indexes:
                batch.drop_index(_index_name(table_name))
            if _fk_name(table_name) in foreign_keys:
                batch.drop_constraint(_fk_name(table_name), type_="foreignkey")
            for column_name in (
                "tenant_assignment_version",
                "tenant_assignment_source",
                "tenant_id",
            ):
                if column_name in columns:
                    batch.drop_column(column_name)

    inspector = sa.inspect(bind)
    if _TENANT_TABLE in inspector.get_table_names():
        tenant_indexes = {item["name"] for item in inspector.get_indexes(_TENANT_TABLE)}
        if "ix_tenants_is_active" in tenant_indexes:
            op.drop_index("ix_tenants_is_active", table_name=_TENANT_TABLE)
        op.drop_table(_TENANT_TABLE)
