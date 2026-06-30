"""round12 external_channel maximization

Revision ID: 20260410_0008
Revises: 20260410_0007
Create Date: 2026-04-10 00:08:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260410_0008"
down_revision = "20260410_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    tables = set(inspector.get_table_names())
    if "channel_accounts" not in tables:
        op.create_table(
            "channel_accounts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("provider", sa.String(length=40), nullable=False),
            sa.Column("account_id", sa.String(length=160), nullable=False),
            sa.Column("display_name", sa.String(length=160), nullable=True),
            sa.Column("market_id", sa.Integer(), sa.ForeignKey("markets.id"), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
            sa.Column("health_status", sa.String(length=40), nullable=False, server_default="unknown"),
            sa.Column("last_health_check_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("fallback_account_id", sa.String(length=160), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_channel_accounts_account_id", "channel_accounts", ["account_id"], unique=True)
        op.create_index("ix_channel_accounts_market_id", "channel_accounts", ["market_id"], unique=False)

    ticket_cols = {col["name"] for col in inspector.get_columns("tickets")}
    if "conversation_state" not in ticket_cols:
        op.add_column("tickets", sa.Column("conversation_state", sa.String(length=64), nullable=False, server_default="ai_active"))
        inspector = sa.inspect(bind)
        ticket_cols = {col["name"] for col in inspector.get_columns("tickets")}
    if "channel_account_id" not in ticket_cols:
        if bind.dialect.name == "sqlite":
            op.add_column("tickets", sa.Column("channel_account_id", sa.Integer(), nullable=True))
        else:
            op.add_column("tickets", sa.Column("channel_account_id", sa.Integer(), sa.ForeignKey("channel_accounts.id"), nullable=True))
        inspector = sa.inspect(bind)
        if "ix_tickets_channel_account_id" not in {idx["name"] for idx in inspector.get_indexes("tickets")}:
            op.create_index("ix_tickets_channel_account_id", "tickets", ["channel_account_id"], unique=False)

    ocl_cols = {col["name"] for col in inspector.get_columns("external_channel_conversation_links")}
    if "channel_account_id" not in ocl_cols:
        if bind.dialect.name == "sqlite":
            op.add_column("external_channel_conversation_links", sa.Column("channel_account_id", sa.Integer(), nullable=True))
        else:
            op.add_column("external_channel_conversation_links", sa.Column("channel_account_id", sa.Integer(), sa.ForeignKey("channel_accounts.id"), nullable=True))
        inspector = sa.inspect(bind)
        if "ix_external_channel_conversation_links_channel_account_id" not in {idx["name"] for idx in inspector.get_indexes("external_channel_conversation_links")}:
            op.create_index("ix_external_channel_conversation_links_channel_account_id", "external_channel_conversation_links", ["channel_account_id"], unique=False)

    if "external_channel_attachment_references" not in tables:
        op.create_table(
            "external_channel_attachment_references",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id"), nullable=False),
            sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("external_channel_conversation_links.id"), nullable=False),
            sa.Column("transcript_message_id", sa.Integer(), sa.ForeignKey("external_channel_transcript_messages.id"), nullable=False),
            sa.Column("remote_attachment_id", sa.String(length=160), nullable=False),
            sa.Column("content_type", sa.String(length=120), nullable=True),
            sa.Column("filename", sa.String(length=255), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("storage_status", sa.String(length=40), nullable=False, server_default="referenced"),
            sa.Column("storage_key", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_external_channel_attachment_refs_ticket_id", "external_channel_attachment_references", ["ticket_id"], unique=False)
        op.create_index("ix_external_channel_attachment_refs_remote_attachment_id", "external_channel_attachment_references", ["remote_attachment_id"], unique=False)

    if "external_channel_sync_cursors" not in tables:
        op.create_table(
            "external_channel_sync_cursors",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("source", sa.String(length=80), nullable=False),
            sa.Column("cursor_value", sa.String(length=255), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_external_channel_sync_cursors_source", "external_channel_sync_cursors", ["source"], unique=True)


def downgrade() -> None:
    pass
