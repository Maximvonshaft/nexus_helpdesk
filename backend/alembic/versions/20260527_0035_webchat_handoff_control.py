"""webchat human handoff control

Revision ID: 20260527_0035
Revises: 20260526_0034
Create Date: 2026-05-27
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260527_0035"
down_revision = "20260526_0034"
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


def _create_index_once(name: str, table_name: str, columns: list[str], *, unique: bool = False, **kwargs) -> None:
    bind = op.get_bind()
    if table_name in _tables(bind) and name not in _indexes(bind, table_name):
        op.create_index(name, table_name, columns, unique=unique, **kwargs)


def upgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)

    if "webchat_handoff_requests" not in tables:
        op.create_table(
            "webchat_handoff_requests",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("conversation_id", sa.Integer(), nullable=False),
            sa.Column("ticket_id", sa.Integer(), nullable=False),
            sa.Column("source", sa.String(length=40), nullable=False, server_default="ai_auto"),
            sa.Column("trigger_type", sa.String(length=80), nullable=False, server_default="handoff_required"),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="requested"),
            sa.Column("reason_code", sa.String(length=160), nullable=True),
            sa.Column("reason_text", sa.Text(), nullable=True),
            sa.Column("recommended_agent_action", sa.Text(), nullable=True),
            sa.Column("trigger_message_id", sa.Integer(), nullable=True),
            sa.Column("ai_turn_id", sa.Integer(), nullable=True),
            sa.Column("requested_by_actor_type", sa.String(length=40), nullable=False, server_default="system"),
            sa.Column("requested_by_user_id", sa.Integer(), nullable=True),
            sa.Column("accepted_by_user_id", sa.Integer(), nullable=True),
            sa.Column("forced_by_user_id", sa.Integer(), nullable=True),
            sa.Column("assigned_agent_id", sa.Integer(), nullable=True),
            sa.Column("decision_note", sa.Text(), nullable=True),
            sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("lock_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["conversation_id"], ["webchat_conversations.id"]),
            sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
            sa.ForeignKeyConstraint(["trigger_message_id"], ["webchat_messages.id"]),
            sa.ForeignKeyConstraint(["ai_turn_id"], ["webchat_ai_turns.id"]),
            sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["accepted_by_user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["forced_by_user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["assigned_agent_id"], ["users.id"]),
        )

    for name, cols in {
        "ix_webchat_handoff_requests_conversation_id": ["conversation_id"],
        "ix_webchat_handoff_requests_ticket_id": ["ticket_id"],
        "ix_webchat_handoff_requests_status": ["status"],
        "ix_webchat_handoff_requests_reason_code": ["reason_code"],
        "ix_webchat_handoff_requests_trigger_message_id": ["trigger_message_id"],
        "ix_webchat_handoff_requests_ai_turn_id": ["ai_turn_id"],
        "ix_webchat_handoff_requests_requested_by_actor_type": ["requested_by_actor_type"],
        "ix_webchat_handoff_requests_requested_by_user_id": ["requested_by_user_id"],
        "ix_webchat_handoff_requests_accepted_by_user_id": ["accepted_by_user_id"],
        "ix_webchat_handoff_requests_forced_by_user_id": ["forced_by_user_id"],
        "ix_webchat_handoff_requests_assigned_agent_id": ["assigned_agent_id"],
        "ix_webchat_handoff_requests_requested_at": ["requested_at"],
        "ix_webchat_handoff_requests_accepted_at": ["accepted_at"],
        "ix_webchat_handoff_requests_released_at": ["released_at"],
        "ix_webchat_handoff_requests_closed_at": ["closed_at"],
        "ix_webchat_handoff_requests_expires_at": ["expires_at"],
        "ix_webchat_handoff_requests_created_at": ["created_at"],
        "ix_webchat_handoff_requests_updated_at": ["updated_at"],
        "ix_webchat_handoff_requests_status_requested": ["status", "requested_at"],
        "ix_webchat_handoff_requests_ticket_status": ["ticket_id", "status"],
        "ix_webchat_handoff_requests_assigned_status": ["assigned_agent_id", "status"],
        "ix_webchat_handoff_requests_source_trigger": ["source", "trigger_type"],
    }.items():
        _create_index_once(name, "webchat_handoff_requests", cols)

    if "uq_webchat_handoff_open_conversation" not in _indexes(bind, "webchat_handoff_requests"):
        op.create_index(
            "uq_webchat_handoff_open_conversation",
            "webchat_handoff_requests",
            ["conversation_id"],
            unique=True,
            postgresql_where=sa.text("status IN ('requested', 'accepted')"),
            sqlite_where=sa.text("status IN ('requested', 'accepted')"),
        )

    if "webchat_handoff_decisions" not in _tables(bind):
        op.create_table(
            "webchat_handoff_decisions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("request_id", sa.Integer(), nullable=False),
            sa.Column("actor_id", sa.Integer(), nullable=False),
            sa.Column("decision", sa.String(length=40), nullable=False, server_default="declined"),
            sa.Column("reason_code", sa.String(length=160), nullable=True),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["request_id"], ["webchat_handoff_requests.id"]),
            sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
        )
    for name, cols in {
        "ix_webchat_handoff_decisions_request_id": ["request_id"],
        "ix_webchat_handoff_decisions_actor_id": ["actor_id"],
        "ix_webchat_handoff_decisions_decision": ["decision"],
        "ix_webchat_handoff_decisions_reason_code": ["reason_code"],
        "ix_webchat_handoff_decisions_created_at": ["created_at"],
        "ix_webchat_handoff_decisions_request_actor": ["request_id", "actor_id"],
        "ix_webchat_handoff_decisions_decision_created": ["decision", "created_at"],
    }.items():
        _create_index_once(name, "webchat_handoff_decisions", cols)

    conversation_cols = _columns(bind, "webchat_conversations")
    for col in [
        sa.Column("current_handoff_request_id", sa.Integer(), nullable=True),
        sa.Column("handoff_status", sa.String(length=40), nullable=False, server_default="none"),
        sa.Column("active_agent_id", sa.Integer(), nullable=True),
        sa.Column("ai_suspended", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("ai_suspended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ai_suspended_by", sa.Integer(), nullable=True),
        sa.Column("ai_suspended_reason", sa.String(length=240), nullable=True),
        sa.Column("takeover_mode", sa.String(length=40), nullable=True),
        sa.Column("last_handoff_reason", sa.String(length=240), nullable=True),
    ]:
        if col.name not in conversation_cols:
            op.add_column("webchat_conversations", col)

    for name, cols in {
        "ix_webchat_conversations_current_handoff_request_id": ["current_handoff_request_id"],
        "ix_webchat_conversations_handoff_status": ["handoff_status"],
        "ix_webchat_conversations_active_agent_id": ["active_agent_id"],
        "ix_webchat_conversations_ai_suspended": ["ai_suspended"],
        "ix_webchat_conversations_ai_suspended_at": ["ai_suspended_at"],
        "ix_webchat_conversations_ai_suspended_by": ["ai_suspended_by"],
        "ix_webchat_conversations_takeover_mode": ["takeover_mode"],
    }.items():
        _create_index_once(name, "webchat_conversations", cols)

    message_cols = _columns(bind, "webchat_messages")
    if "author_user_id" not in message_cols:
        op.add_column("webchat_messages", sa.Column("author_user_id", sa.Integer(), nullable=True))
    _create_index_once("ix_webchat_messages_author_user_id", "webchat_messages", ["author_user_id"])

    turn_cols = _columns(bind, "webchat_ai_turns")
    if "cancelled_by_user_id" not in turn_cols:
        op.add_column("webchat_ai_turns", sa.Column("cancelled_by_user_id", sa.Integer(), nullable=True))
    if "cancellation_reason_code" not in turn_cols:
        op.add_column("webchat_ai_turns", sa.Column("cancellation_reason_code", sa.String(length=120), nullable=True))
    _create_index_once("ix_webchat_ai_turns_cancelled_by_user_id", "webchat_ai_turns", ["cancelled_by_user_id"])
    _create_index_once("ix_webchat_ai_turns_cancellation_reason_code", "webchat_ai_turns", ["cancellation_reason_code"])


def downgrade() -> None:
    bind = op.get_bind()
    if "webchat_ai_turns" in _tables(bind):
        for name in ["ix_webchat_ai_turns_cancellation_reason_code", "ix_webchat_ai_turns_cancelled_by_user_id"]:
            if name in _indexes(bind, "webchat_ai_turns"):
                op.drop_index(name, table_name="webchat_ai_turns")
        for col in ["cancellation_reason_code", "cancelled_by_user_id"]:
            if col in _columns(bind, "webchat_ai_turns"):
                op.drop_column("webchat_ai_turns", col)

    if "webchat_messages" in _tables(bind):
        if "ix_webchat_messages_author_user_id" in _indexes(bind, "webchat_messages"):
            op.drop_index("ix_webchat_messages_author_user_id", table_name="webchat_messages")
        if "author_user_id" in _columns(bind, "webchat_messages"):
            op.drop_column("webchat_messages", "author_user_id")

    if "webchat_conversations" in _tables(bind):
        for name in [
            "ix_webchat_conversations_takeover_mode",
            "ix_webchat_conversations_ai_suspended_by",
            "ix_webchat_conversations_ai_suspended_at",
            "ix_webchat_conversations_ai_suspended",
            "ix_webchat_conversations_active_agent_id",
            "ix_webchat_conversations_handoff_status",
            "ix_webchat_conversations_current_handoff_request_id",
        ]:
            if name in _indexes(bind, "webchat_conversations"):
                op.drop_index(name, table_name="webchat_conversations")
        for col in [
            "last_handoff_reason",
            "takeover_mode",
            "ai_suspended_reason",
            "ai_suspended_by",
            "ai_suspended_at",
            "ai_suspended",
            "active_agent_id",
            "handoff_status",
            "current_handoff_request_id",
        ]:
            if col in _columns(bind, "webchat_conversations"):
                op.drop_column("webchat_conversations", col)

    if "webchat_handoff_decisions" in _tables(bind):
        op.drop_table("webchat_handoff_decisions")
    if "webchat_handoff_requests" in _tables(bind):
        op.drop_table("webchat_handoff_requests")
