"""add server-owned public WebChat origin bindings

Revision ID: 20260711_0058
Revises: 20260711_0057
Create Date: 2026-07-11

The table contains routing authorization only. It stores no visitor token,
customer message, tracking identifier, provider payload or credential.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260711_0058"
down_revision = "20260711_0057"
branch_labels = None
depends_on = None

_TABLE = "webchat_public_origin_bindings"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE in inspector.get_table_names():
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("normalized_origin", sa.String(length=255), nullable=False),
        sa.Column("tenant_key", sa.String(length=120), nullable=False),
        sa.Column("channel_key", sa.String(length=120), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized_origin", name="uq_webchat_public_origin_binding_origin"),
    )
    op.create_index(
        "ix_webchat_public_origin_bindings_normalized_origin",
        _TABLE,
        ["normalized_origin"],
    )
    op.create_index(
        "ix_webchat_public_origin_bindings_tenant_key",
        _TABLE,
        ["tenant_key"],
    )
    op.create_index(
        "ix_webchat_public_origin_bindings_channel_key",
        _TABLE,
        ["channel_key"],
    )
    op.create_index(
        "ix_webchat_public_origin_bindings_is_active",
        _TABLE,
        ["is_active"],
    )
    op.create_index(
        "ix_webchat_public_origin_binding_scope",
        _TABLE,
        ["tenant_key", "channel_key", "is_active"],
    )
    op.create_index(
        "ix_webchat_public_origin_bindings_created_by",
        _TABLE,
        ["created_by"],
    )
    op.create_index(
        "ix_webchat_public_origin_bindings_updated_by",
        _TABLE,
        ["updated_by"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in inspector.get_table_names():
        return
    for index_name in (
        "ix_webchat_public_origin_bindings_updated_by",
        "ix_webchat_public_origin_bindings_created_by",
        "ix_webchat_public_origin_binding_scope",
        "ix_webchat_public_origin_bindings_is_active",
        "ix_webchat_public_origin_bindings_channel_key",
        "ix_webchat_public_origin_bindings_tenant_key",
        "ix_webchat_public_origin_bindings_normalized_origin",
    ):
        if index_name in {item["name"] for item in sa.inspect(bind).get_indexes(_TABLE)}:
            op.drop_index(index_name, table_name=_TABLE)
    op.drop_table(_TABLE)
