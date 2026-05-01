"""add ai config resources tables

Revision ID: 20260502_0014
Revises: 20260501_0013
Create Date: 2026-05-02
"""

from alembic import op
import sqlalchemy as sa


revision = "20260502_0014"
down_revision = "20260501_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_config_resources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("resource_key", sa.String(length=120), nullable=False),
        sa.Column("config_type", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("scope_type", sa.String(length=40), nullable=False, server_default="global"),
        sa.Column("scope_value", sa.String(length=160), nullable=True),
        sa.Column("market_id", sa.Integer(), sa.ForeignKey("markets.id"), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("draft_summary", sa.Text(), nullable=True),
        sa.Column("draft_content_json", sa.JSON(), nullable=True),
        sa.Column("published_summary", sa.Text(), nullable=True),
        sa.Column("published_content_json", sa.JSON(), nullable=True),
        sa.Column("published_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("published_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for name, columns, unique in [
        ("ix_ai_config_resources_resource_key", ["resource_key"], True),
        ("ix_ai_config_resources_config_type", ["config_type"], False),
        ("ix_ai_config_resources_name", ["name"], False),
        ("ix_ai_config_resources_scope_type", ["scope_type"], False),
        ("ix_ai_config_resources_scope_value", ["scope_value"], False),
        ("ix_ai_config_resources_market_id", ["market_id"], False),
        ("ix_ai_config_resources_is_active", ["is_active"], False),
        ("ix_ai_config_resources_published_at", ["published_at"], False),
        ("ix_ai_config_resources_created_by", ["created_by"], False),
        ("ix_ai_config_resources_updated_by", ["updated_by"], False),
        ("ix_ai_config_resources_published_by", ["published_by"], False),
        ("ix_ai_config_resources_created_at", ["created_at"], False),
        ("ix_ai_config_resources_updated_at", ["updated_at"], False),
    ]:
        op.create_index(name, "ai_config_resources", columns, unique=unique)

    op.create_table(
        "ai_config_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("resource_id", sa.Integer(), sa.ForeignKey("ai_config_resources.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("published_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("resource_id", "version", name="uq_ai_config_resource_version"),
    )
    for name, columns in [
        ("ix_ai_config_versions_resource_id", ["resource_id"]),
        ("ix_ai_config_versions_version", ["version"]),
        ("ix_ai_config_versions_published_by", ["published_by"]),
        ("ix_ai_config_versions_published_at", ["published_at"]),
    ]:
        op.create_index(name, "ai_config_versions", columns)


def downgrade() -> None:
    # Forward-only production safety: this migration repairs missing production schema.
    # If rollback is required after production data is written, use an explicit audited
    # rollback plan instead of automatically removing AI config data.
    pass
