"""add exact operator queue visibility grants

Revision ID: 20260711_0057
Revises: 20260710_0056
Create Date: 2026-07-11

The table is an authorization policy only. It stores no queue projection,
customer content, provider payload, tracking identifier or credential.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260711_0057"
down_revision = "20260710_0056"
branch_labels = None
depends_on = None

_TABLE = "operator_queue_scope_grants"
_INDEXES = (
    "ix_operator_queue_scope_grants_user_id",
    "ix_operator_queue_scope_grants_granted_by",
    "ix_operator_queue_scope_grants_user_enabled",
    "ix_operator_queue_scope_grants_scope",
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE in inspector.get_table_names():
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tenant_key", sa.String(length=80), nullable=False),
        sa.Column("country_code", sa.String(length=16), nullable=False),
        sa.Column("channel_key", sa.String(length=40), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("granted_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["granted_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "tenant_key",
            "country_code",
            "channel_key",
            name="uq_operator_queue_scope_grant",
        ),
    )
    op.create_index("ix_operator_queue_scope_grants_user_id", _TABLE, ["user_id"])
    op.create_index("ix_operator_queue_scope_grants_granted_by", _TABLE, ["granted_by"])
    op.create_index("ix_operator_queue_scope_grants_user_enabled", _TABLE, ["user_id", "enabled"])
    op.create_index(
        "ix_operator_queue_scope_grants_scope",
        _TABLE,
        ["tenant_key", "country_code", "channel_key", "enabled"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in inspector.get_table_names():
        return
    existing = {index["name"] for index in inspector.get_indexes(_TABLE)}
    for name in reversed(_INDEXES):
        if name in existing:
            op.drop_index(name, table_name=_TABLE)
    op.drop_table(_TABLE)
