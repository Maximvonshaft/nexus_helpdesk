"""webchat ai runtime p0 idempotency hardening

Revision ID: 20260505_0017
Revises: 20260503_0016
Create Date: 2026-05-05
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260505_0017"
down_revision = "20260503_0016"
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


def _add_column_if_missing(bind, table: str, column: sa.Column) -> None:
    if column.name not in _columns(bind, table):
        op.add_column(table, column)


def _create_index_if_missing(bind, name: str, table: str, cols: list[str], *, unique: bool = False, **kwargs) -> None:
    if name not in _indexes(bind, table):
        op.create_index(name, table, cols, unique=unique, **kwargs)


def _has_duplicate_visitor_client_messages(bind) -> bool:
    sql = sa.text(
        """
        SELECT 1
        FROM webchat_messages
        WHERE client_message_id IS NOT NULL AND direction = 'visitor'
        GROUP BY conversation_id, client_message_id, direction
        HAVING COUNT(*) > 1
        LIMIT 1
        """
    )
    return bind.execute(sql).first() is not None


def _has_duplicate_agent_ai_turns(bind) -> bool:
    if "ai_turn_id" not in _columns(bind, "webchat_messages"):
        return False
    sql = sa.text(
        """
        SELECT 1
        FROM webchat_messages
        WHERE ai_turn_id IS NOT NULL AND direction = 'agent'
        GROUP BY ai_turn_id
        HAVING COUNT(*) > 1
        LIMIT 1
        """
    )
    return bind.execute(sql).first() is not None


def _ensure_turn_runtime_tables(bind) -> None:
    existing_tables = _tables(bind)
    if "webchat_ai_turns" not in existing_tables:
        op.create_table(
            "webchat_ai_turns",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("webchat_conversations.id"), nullable=False),
            sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id"), nullable=False),
            sa.Column("trigger_message_id", sa.Integer(), sa.ForeignKey("webchat_messages.id"), nullable=False),
            sa.Column("latest_visitor_message_id", sa.Integer(), sa.ForeignKey("webchat_messages.id"), nullable=True),
            sa.Column("context_cutoff_message_id", sa.Integer(), sa.ForeignKey("webchat_messages.id"), nullable=True),
            sa.Column("job_id", sa.Integer(), sa.ForeignKey("background_jobs.id"), nullable=True),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="queued"),
            sa.Column("status_reason", sa.Text(), nullable=True),
            sa.Column("reply_message_id", sa.Integer(), sa.ForeignKey("webchat_messages.id"), nullable=True),
            sa.Column("reply_source", sa.String(length=80), nullable=True),
            sa.Column("fallback_reason", sa.Text(), nullable=True),
            sa.Column("fact_gate_reason", sa.Text(), nullable=True),
            sa.Column("bridge_elapsed_ms", sa.Integer(), nullable=True),
            sa.Column("bridge_timeout_ms", sa.Integer(), nullable=True),
            sa.Column("superseded_by_turn_id", sa.Integer(), sa.ForeignKey("webchat_ai_turns.id"), nullable=True),
            sa.Column("is_public_reply_allowed", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    _create_index_if_missing(bind, "uq_webchat_ai_turn_trigger_message", "webchat_ai_turns", ["trigger_message_id"], unique=True)
    for name, cols in {
        "ix_webchat_ai_turns_conversation_id": ["conversation_id"],
        "ix_webchat_ai_turns_ticket_id": ["ticket_id"],
        "ix_webchat_ai_turns_trigger_message_id": ["trigger_message_id"],
        "ix_webchat_ai_turns_latest_visitor_message_id": ["latest_visitor_message_id"],
        "ix_webchat_ai_turns_context_cutoff_message_id": ["context_cutoff_message_id"],
        "ix_webchat_ai_turns_job_id": ["job_id"],
        "ix_webchat_ai_turns_status": ["status"],
        "ix_webchat_ai_turns_reply_message_id": ["reply_message_id"],
        "ix_webchat_ai_turns_reply_source": ["reply_source"],
        "ix_webchat_ai_turns_superseded_by_turn_id": ["superseded_by_turn_id"],
        "ix_webchat_ai_turns_is_public_reply_allowed": ["is_public_reply_allowed"],
        "ix_webchat_ai_turns_started_at": ["started_at"],
        "ix_webchat_ai_turns_completed_at": ["completed_at"],
        "ix_webchat_ai_turns_updated_at": ["updated_at"],
    }.items():
        _create_index_if_missing(bind, name, "webchat_ai_turns", cols)

    if "webchat_events" not in _tables(bind):
        op.create_table(
            "webchat_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("webchat_conversations.id"), nullable=False),
            sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id"), nullable=False),
            sa.Column("event_type", sa.String(length=80), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    for name, cols in {
        "ix_webchat_events_conversation_id": ["conversation_id"],
        "ix_webchat_events_ticket_id": ["ticket_id"],
        "ix_webchat_events_event_type": ["event_type"],
        "ix_webchat_events_created_at": ["created_at"],
    }.items():
        _create_index_if_missing(bind, name, "webchat_events", cols)


def _ensure_conversation_snapshot_columns(bind) -> None:
    if "webchat_conversations" not in _tables(bind):
        return
    _add_column_if_missing(bind, "webchat_conversations", sa.Column("active_ai_turn_id", sa.Integer(), nullable=True))
    _add_column_if_missing(bind, "webchat_conversations", sa.Column("active_ai_status", sa.String(length=40), nullable=True))
    _add_column_if_missing(bind, "webchat_conversations", sa.Column("active_ai_for_message_id", sa.Integer(), nullable=True))
    _add_column_if_missing(bind, "webchat_conversations", sa.Column("active_ai_context_cutoff_message_id", sa.Integer(), nullable=True))
    _add_column_if_missing(bind, "webchat_conversations", sa.Column("next_ai_turn_id", sa.Integer(), nullable=True))
    _add_column_if_missing(bind, "webchat_conversations", sa.Column("active_ai_started_at", sa.DateTime(timezone=True), nullable=True))
    _add_column_if_missing(bind, "webchat_conversations", sa.Column("active_ai_updated_at", sa.DateTime(timezone=True), nullable=True))
    for name, cols in {
        "ix_webchat_conversations_active_ai_turn_id": ["active_ai_turn_id"],
        "ix_webchat_conversations_active_ai_status": ["active_ai_status"],
        "ix_webchat_conversations_active_ai_for_message_id": ["active_ai_for_message_id"],
        "ix_webchat_conversations_active_ai_context_cutoff_message_id": ["active_ai_context_cutoff_message_id"],
        "ix_webchat_conversations_next_ai_turn_id": ["next_ai_turn_id"],
        "ix_webchat_conversations_active_ai_updated_at": ["active_ai_updated_at"],
    }.items():
        _create_index_if_missing(bind, name, "webchat_conversations", cols)


def upgrade() -> None:
    bind = op.get_bind()
    if "webchat_messages" not in _tables(bind):
        return

    _ensure_turn_runtime_tables(bind)
    _ensure_conversation_snapshot_columns(bind)

    if _has_duplicate_visitor_client_messages(bind):
        raise RuntimeError(
            "Cannot create uq_webchat_visitor_client_message: duplicate visitor client_message_id rows exist. "
            "Run the read-only duplicate detection SQL and clean/merge historical duplicates first."
        )

    _add_column_if_missing(
        bind,
        "webchat_messages",
        sa.Column("ai_turn_id", sa.Integer(), sa.ForeignKey("webchat_ai_turns.id"), nullable=True),
    )
    _create_index_if_missing(bind, "ix_webchat_messages_ai_turn_id", "webchat_messages", ["ai_turn_id"])

    if _has_duplicate_agent_ai_turns(bind):
        raise RuntimeError(
            "Cannot create uq_webchat_agent_ai_turn: duplicate agent replies already reference the same ai_turn_id. "
            "Run the read-only duplicate detection SQL and clean/merge historical duplicates first."
        )

    dialect = bind.dialect.name
    visitor_where = sa.text("client_message_id IS NOT NULL AND direction = 'visitor'")
    agent_where = sa.text("ai_turn_id IS NOT NULL AND direction = 'agent'")
    visitor_kwargs = {}
    agent_kwargs = {}
    if dialect.startswith("postgresql"):
        visitor_kwargs["postgresql_where"] = visitor_where
        agent_kwargs["postgresql_where"] = agent_where
    elif dialect == "sqlite":
        visitor_kwargs["sqlite_where"] = visitor_where
        agent_kwargs["sqlite_where"] = agent_where

    _create_index_if_missing(
        bind,
        "uq_webchat_visitor_client_message",
        "webchat_messages",
        ["conversation_id", "client_message_id", "direction"],
        unique=True,
        **visitor_kwargs,
    )
    _create_index_if_missing(
        bind,
        "uq_webchat_agent_ai_turn",
        "webchat_messages",
        ["ai_turn_id"],
        unique=True,
        **agent_kwargs,
    )


def downgrade() -> None:
    bind = op.get_bind()
    if "webchat_messages" in _tables(bind):
        indexes = _indexes(bind, "webchat_messages")
        for name in ["uq_webchat_agent_ai_turn", "uq_webchat_visitor_client_message", "ix_webchat_messages_ai_turn_id"]:
            if name in indexes:
                op.drop_index(name, table_name="webchat_messages")
        if "ai_turn_id" in _columns(bind, "webchat_messages"):
            with op.batch_alter_table("webchat_messages") as batch_op:
                batch_op.drop_column("ai_turn_id")

    if "webchat_conversations" in _tables(bind):
        indexes = _indexes(bind, "webchat_conversations")
        for name in [
            "ix_webchat_conversations_active_ai_updated_at",
            "ix_webchat_conversations_next_ai_turn_id",
            "ix_webchat_conversations_active_ai_context_cutoff_message_id",
            "ix_webchat_conversations_active_ai_for_message_id",
            "ix_webchat_conversations_active_ai_status",
            "ix_webchat_conversations_active_ai_turn_id",
        ]:
            if name in indexes:
                op.drop_index(name, table_name="webchat_conversations")
        existing_cols = _columns(bind, "webchat_conversations")
        with op.batch_alter_table("webchat_conversations") as batch_op:
            for col in ["active_ai_updated_at", "active_ai_started_at", "next_ai_turn_id", "active_ai_context_cutoff_message_id", "active_ai_for_message_id", "active_ai_status", "active_ai_turn_id"]:
                if col in existing_cols:
                    batch_op.drop_column(col)

    if "webchat_events" in _tables(bind):
        op.drop_table("webchat_events")
    if "webchat_ai_turns" in _tables(bind):
        op.drop_table("webchat_ai_turns")
