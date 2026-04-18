"""round23 schema governance and audit hardening

Revision ID: 20260410_0011
Revises: 20260410_0010
Create Date: 2026-04-13 00:11:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260410_0011"
down_revision = "20260410_0010"
branch_labels = None
depends_on = None


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _columns(bind, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return set()
    return {col["name"] for col in inspector.get_columns(table_name)}


def _indexes(bind, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return set()
    return {idx["name"] for idx in inspector.get_indexes(table_name)}


def _create_index_if_missing(bind, name: str, table_name: str, columns: list[str], *, unique: bool = False) -> None:
    if name not in _indexes(bind, table_name):
        op.create_index(name, table_name, columns, unique=unique)


def upgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)

    if "integration_request_logs" in tables:
        cols = _columns(bind, "integration_request_logs")
        if "error_code" not in cols:
            op.add_column("integration_request_logs", sa.Column("error_code", sa.String(length=120), nullable=True))
            cols = _columns(bind, "integration_request_logs")
        _create_index_if_missing(bind, "ix_integration_request_logs_error_code", "integration_request_logs", ["error_code"], unique=False)

    if "markets" in tables:
        _create_index_if_missing(bind, "ix_markets_name", "markets", ["name"], unique=True)

    if "channel_accounts" in tables:
        _create_index_if_missing(bind, "ix_channel_accounts_provider", "channel_accounts", ["provider"], unique=False)

    if "openclaw_conversation_links" in tables:
        _create_index_if_missing(bind, "ix_openclaw_conversation_links_ticket_id", "openclaw_conversation_links", ["ticket_id"], unique=False)
        _create_index_if_missing(bind, "ix_openclaw_conversation_links_created_at", "openclaw_conversation_links", ["created_at"], unique=False)

    if "openclaw_transcript_messages" in tables:
        _create_index_if_missing(bind, "ix_openclaw_transcript_messages_conversation_id", "openclaw_transcript_messages", ["conversation_id"], unique=False)
        _create_index_if_missing(bind, "ix_openclaw_transcript_messages_created_at", "openclaw_transcript_messages", ["created_at"], unique=False)
        _create_index_if_missing(bind, "ix_openclaw_transcript_messages_ticket_id", "openclaw_transcript_messages", ["ticket_id"], unique=False)

    if "openclaw_attachment_references" in tables:
        _create_index_if_missing(bind, "ix_openclaw_attachment_references_ticket_id", "openclaw_attachment_references", ["ticket_id"], unique=False)
        _create_index_if_missing(bind, "ix_openclaw_attachment_references_conversation_id", "openclaw_attachment_references", ["conversation_id"], unique=False)
        _create_index_if_missing(bind, "ix_openclaw_attachment_references_transcript_message_id", "openclaw_attachment_references", ["transcript_message_id"], unique=False)
        _create_index_if_missing(bind, "ix_openclaw_attachment_references_remote_attachment_id", "openclaw_attachment_references", ["remote_attachment_id"], unique=False)

    if "market_bulletins" in tables:
        _create_index_if_missing(bind, "ix_market_bulletins_category", "market_bulletins", ["category"], unique=False)
        _create_index_if_missing(bind, "ix_market_bulletins_created_by", "market_bulletins", ["created_by"], unique=False)
        _create_index_if_missing(bind, "ix_market_bulletins_ends_at", "market_bulletins", ["ends_at"], unique=False)
        _create_index_if_missing(bind, "ix_market_bulletins_is_active", "market_bulletins", ["is_active"], unique=False)
        _create_index_if_missing(bind, "ix_market_bulletins_starts_at", "market_bulletins", ["starts_at"], unique=False)
        _create_index_if_missing(bind, "ix_market_bulletins_title", "market_bulletins", ["title"], unique=False)

    if "ticket_ai_intakes" not in tables:
        op.create_table(
            "ticket_ai_intakes",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id"), nullable=False),
            sa.Column("summary", sa.Text(), nullable=False),
            sa.Column("classification", sa.String(length=120), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("missing_fields_json", sa.Text(), nullable=True),
            sa.Column("recommended_action", sa.Text(), nullable=True),
            sa.Column("suggested_reply", sa.Text(), nullable=True),
            sa.Column("raw_payload_json", sa.Text(), nullable=True),
            sa.Column("human_override_reason", sa.Text(), nullable=True),
            sa.Column("market_id", sa.Integer(), sa.ForeignKey("markets.id"), nullable=True),
            sa.Column("country_code", sa.String(length=8), nullable=True),
            sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    if "ticket_ai_intakes" in _tables(bind):
        _create_index_if_missing(bind, "ix_ticket_ai_intakes_ticket_id", "ticket_ai_intakes", ["ticket_id"], unique=False)
        _create_index_if_missing(bind, "ix_ticket_ai_intakes_created_at", "ticket_ai_intakes", ["created_at"], unique=False)
        _create_index_if_missing(bind, "ix_ticket_ai_intakes_created_by", "ticket_ai_intakes", ["created_by"], unique=False)
        _create_index_if_missing(bind, "ix_ticket_ai_intakes_market_id", "ticket_ai_intakes", ["market_id"], unique=False)
        _create_index_if_missing(bind, "ix_ticket_ai_intakes_country_code", "ticket_ai_intakes", ["country_code"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if "integration_request_logs" in _tables(bind):
        if "ix_integration_request_logs_error_code" in _indexes(bind, "integration_request_logs"):
            op.drop_index("ix_integration_request_logs_error_code", table_name="integration_request_logs")
        if "error_code" in _columns(bind, "integration_request_logs"):
            op.drop_column("integration_request_logs", "error_code")
