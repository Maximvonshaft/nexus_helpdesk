"""tool governance audit-only baseline

Revision ID: 20260506_0018
Revises: 20260505_0017
Create Date: 2026-05-06
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260506_0018"
down_revision = "20260505_0017"
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


def _create_index_if_missing(bind, name: str, table: str, cols: list[str], *, unique: bool = False) -> None:
    if name not in _indexes(bind, table):
        op.create_index(name, table, cols, unique=unique)


def _create_tool_registry(bind) -> None:
    if "tool_registry" not in _tables(bind):
        op.create_table(
            "tool_registry",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("tool_name", sa.String(length=160), nullable=False),
            sa.Column("provider", sa.String(length=80), nullable=False, server_default="openclaw"),
            sa.Column("tool_type", sa.String(length=40), nullable=False, server_default="read_only"),
            sa.Column("capability_scope", sa.String(length=160), nullable=True),
            sa.Column("default_timeout_ms", sa.Integer(), nullable=True),
            sa.Column("max_timeout_ms", sa.Integer(), nullable=True),
            sa.Column("retry_policy", sa.String(length=80), nullable=True),
            sa.Column("risk_level", sa.String(length=40), nullable=False, server_default="low"),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("audit_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("tool_name", name="uq_tool_registry_tool_name"),
        )
    for name, cols in {
        "ix_tool_registry_tool_name": ["tool_name"],
        "ix_tool_registry_provider": ["provider"],
        "ix_tool_registry_tool_type": ["tool_type"],
        "ix_tool_registry_risk_level": ["risk_level"],
        "ix_tool_registry_enabled": ["enabled"],
    }.items():
        _create_index_if_missing(bind, name, "tool_registry", cols)


def _create_tool_call_logs(bind) -> None:
    if "tool_call_logs" not in _tables(bind):
        op.create_table(
            "tool_call_logs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("tool_name", sa.String(length=160), nullable=False),
            sa.Column("provider", sa.String(length=80), nullable=False, server_default="openclaw"),
            sa.Column("tool_type", sa.String(length=40), nullable=False, server_default="read_only"),
            sa.Column("conversation_id", sa.String(length=160), nullable=True),
            sa.Column("webchat_conversation_id", sa.Integer(), nullable=True),
            sa.Column("ticket_id", sa.Integer(), nullable=True),
            sa.Column("ai_turn_id", sa.Integer(), nullable=True),
            sa.Column("background_job_id", sa.Integer(), nullable=True),
            sa.Column("actor_type", sa.String(length=80), nullable=True),
            sa.Column("actor_id", sa.Integer(), nullable=True),
            sa.Column("request_id", sa.String(length=160), nullable=True),
            sa.Column("input_hash", sa.String(length=64), nullable=True),
            sa.Column("input_summary", sa.Text(), nullable=True),
            sa.Column("output_hash", sa.String(length=64), nullable=True),
            sa.Column("output_summary", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="success"),
            sa.Column("error_code", sa.String(length=120), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("elapsed_ms", sa.Integer(), nullable=True),
            sa.Column("timeout_ms", sa.Integer(), nullable=True),
            sa.Column("redaction_applied", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    for name, cols in {
        "ix_tool_call_logs_tool_name": ["tool_name"],
        "ix_tool_call_logs_provider": ["provider"],
        "ix_tool_call_logs_tool_type": ["tool_type"],
        "ix_tool_call_logs_ticket_id": ["ticket_id"],
        "ix_tool_call_logs_ai_turn_id": ["ai_turn_id"],
        "ix_tool_call_logs_status": ["status"],
        "ix_tool_call_logs_created_at": ["created_at"],
        "ix_tool_call_logs_tool_status_created": ["tool_name", "status", "created_at"],
    }.items():
        _create_index_if_missing(bind, name, "tool_call_logs", cols)


def _create_tool_capabilities(bind) -> None:
    if "tool_capabilities" not in _tables(bind):
        op.create_table(
            "tool_capabilities",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("capability", sa.String(length=160), nullable=False),
            sa.Column("tool_name", sa.String(length=160), nullable=False),
            sa.Column("allowed", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("capability", "tool_name", name="uq_tool_capability_tool"),
        )
    for name, cols in {
        "ix_tool_capabilities_capability": ["capability"],
        "ix_tool_capabilities_tool_name": ["tool_name"],
        "ix_tool_capabilities_allowed": ["allowed"],
    }.items():
        _create_index_if_missing(bind, name, "tool_capabilities", cols)


def upgrade() -> None:
    bind = op.get_bind()
    _create_tool_registry(bind)
    _create_tool_call_logs(bind)
    _create_tool_capabilities(bind)


def downgrade() -> None:
    bind = op.get_bind()
    for table in ["tool_capabilities", "tool_call_logs", "tool_registry"]:
        if table in _tables(bind):
            op.drop_table(table)
