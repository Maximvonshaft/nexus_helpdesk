"""add one-time Conversation-bound Agent Tool confirmations

Revision ID: 20260722_tel4
Revises: 20260722_tel3
Create Date: 2026-07-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260722_tel4"
down_revision = "20260722_tel3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_tool_confirmations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_key", sa.String(length=80), nullable=False),
        sa.Column(
            "conversation_id",
            sa.Integer(),
            sa.ForeignKey("webchat_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tool_name", sa.String(length=160), nullable=False),
        sa.Column("arguments_sha256", sa.String(length=64), nullable=False),
        sa.Column("encrypted_arguments", sa.Text(), nullable=False),
        sa.Column("safe_summary_json", sa.JSON(), nullable=False),
        sa.Column("question_text", sa.String(length=1000), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column(
            "requested_message_id",
            sa.Integer(),
            sa.ForeignKey("webchat_messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "response_message_id",
            sa.Integer(),
            sa.ForeignKey("webchat_messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "consumed_tool_call_log_id",
            sa.Integer(),
            sa.ForeignKey("tool_call_logs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'confirmed', 'denied', 'expired', 'consumed', 'cancelled')",
            name="ck_agent_tool_confirmation_status",
        ),
        sa.UniqueConstraint("public_id", name="uq_agent_tool_confirmation_public_id"),
    )
    for column in (
        "public_id",
        "tenant_key",
        "conversation_id",
        "tool_name",
        "arguments_sha256",
        "status",
        "requested_message_id",
        "response_message_id",
        "consumed_tool_call_log_id",
        "requested_at",
        "expires_at",
        "resolved_at",
        "consumed_at",
        "created_at",
        "updated_at",
    ):
        op.create_index(
            f"ix_agent_tool_confirmations_{column}",
            "agent_tool_confirmations",
            [column],
        )
    op.create_index(
        "ix_agent_tool_confirmation_lookup",
        "agent_tool_confirmations",
        ["conversation_id", "status", "expires_at"],
    )
    op.create_index(
        "uq_agent_tool_confirmation_active_conversation",
        "agent_tool_confirmations",
        ["conversation_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('pending', 'confirmed')"),
        postgresql_where=sa.text("status IN ('pending', 'confirmed')"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_agent_tool_confirmation_active_conversation",
        table_name="agent_tool_confirmations",
    )
    op.drop_index(
        "ix_agent_tool_confirmation_lookup",
        table_name="agent_tool_confirmations",
    )
    for column in reversed(
        (
            "public_id",
            "tenant_key",
            "conversation_id",
            "tool_name",
            "arguments_sha256",
            "status",
            "requested_message_id",
            "response_message_id",
            "consumed_tool_call_log_id",
            "requested_at",
            "expires_at",
            "resolved_at",
            "consumed_at",
            "created_at",
            "updated_at",
        )
    ):
        op.drop_index(
            f"ix_agent_tool_confirmations_{column}",
            table_name="agent_tool_confirmations",
        )
    op.drop_table("agent_tool_confirmations")
