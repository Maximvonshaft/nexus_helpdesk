"""nexus osr persistence

Revision ID: 20260709_0054
Revises: 20260708_0053
Create Date: 2026-07-09
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260709_0054"
down_revision = "20260708_0053"
branch_labels = None
depends_on = None


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _indexes(bind, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return set()
    return {idx["name"] for idx in inspector.get_indexes(table_name)}


def _create_index_once(name: str, table_name: str, columns: list[str], *, unique: bool = False) -> None:
    bind = op.get_bind()
    if table_name in _tables(bind) and name not in _indexes(bind, table_name):
        op.create_index(name, table_name, columns, unique=unique)


def upgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)

    if "case_contexts" not in tables:
        op.create_table(
            "case_contexts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("tenant_id", sa.String(length=80), nullable=False, server_default="default"),
            sa.Column("conversation_id", sa.Integer(), nullable=True),
            sa.Column("ticket_id", sa.Integer(), nullable=True),
            sa.Column("channel", sa.String(length=40), nullable=True),
            sa.Column("country_code", sa.String(length=16), nullable=True),
            sa.Column("issue_type", sa.String(length=120), nullable=True),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="active"),
            sa.Column("safe_tracking_reference", sa.String(length=80), nullable=True),
            sa.Column("tracking_number_hash", sa.String(length=128), nullable=True),
            sa.Column("contact_methods_json", sa.JSON(), nullable=True),
            sa.Column("customer_claim_summary", sa.Text(), nullable=True),
            sa.Column("last_mcp_fact_json", sa.JSON(), nullable=True),
            sa.Column("missing_info_json", sa.JSON(), nullable=True),
            sa.Column("handoff_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("ticket_created", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("routed_group_key", sa.String(length=160), nullable=True),
            sa.Column("ai_actions_taken_json", sa.JSON(), nullable=True),
            sa.Column("agent_handover_summary", sa.Text(), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["conversation_id"], ["webchat_conversations.id"]),
            sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
            sa.UniqueConstraint("conversation_id", "ticket_id", name="uq_case_context_conversation_ticket"),
        )
    for name, cols in {
        "ix_case_contexts_tenant_id": ["tenant_id"],
        "ix_case_contexts_conversation_id": ["conversation_id"],
        "ix_case_contexts_ticket_id": ["ticket_id"],
        "ix_case_contexts_channel": ["channel"],
        "ix_case_contexts_country_code": ["country_code"],
        "ix_case_contexts_issue_type": ["issue_type"],
        "ix_case_contexts_status": ["status"],
        "ix_case_contexts_tracking_number_hash": ["tracking_number_hash"],
        "ix_case_contexts_routed_group_key": ["routed_group_key"],
        "ix_case_contexts_expires_at": ["expires_at"],
        "ix_case_contexts_closed_at": ["closed_at"],
        "ix_case_contexts_updated_at": ["updated_at"],
    }.items():
        _create_index_once(name, "case_contexts", cols)

    if "human_hours_policies" not in tables:
        op.create_table(
            "human_hours_policies",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("country_code", sa.String(length=16), nullable=False, server_default="GLOBAL"),
            sa.Column("channel", sa.String(length=40), nullable=False, server_default="all"),
            sa.Column("queue_key", sa.String(length=120), nullable=False),
            sa.Column("timezone_name", sa.String(length=80), nullable=False, server_default="UTC"),
            sa.Column("working_hours_json", sa.JSON(), nullable=True),
            sa.Column("holiday_calendar_json", sa.JSON(), nullable=True),
            sa.Column("handoff_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("offline_message_template", sa.Text(), nullable=True),
            sa.Column("auto_ticket_when_offline", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("customer_wait_timeout_seconds", sa.Integer(), nullable=False, server_default="180"),
            sa.Column("fallback_action", sa.String(length=80), nullable=False, server_default="create_ticket"),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("country_code", "channel", "queue_key", name="uq_human_hours_country_channel_queue"),
        )
    for name, cols in {
        "ix_human_hours_policies_country_code": ["country_code"],
        "ix_human_hours_policies_channel": ["channel"],
        "ix_human_hours_policies_queue_key": ["queue_key"],
        "ix_human_hours_policies_enabled": ["enabled"],
    }.items():
        _create_index_once(name, "human_hours_policies", cols)

    if "escalation_policies" not in tables:
        op.create_table(
            "escalation_policies",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("risk_key", sa.String(length=120), nullable=False),
            sa.Column("country_code", sa.String(length=16), nullable=False, server_default="GLOBAL"),
            sa.Column("channel", sa.String(length=40), nullable=False, server_default="all"),
            sa.Column("trigger_patterns_json", sa.JSON(), nullable=True),
            sa.Column("semantic_intents_json", sa.JSON(), nullable=True),
            sa.Column("max_ai_attempts", sa.Integer(), nullable=False, server_default="2"),
            sa.Column("action", sa.String(length=80), nullable=False, server_default="handoff_or_ticket"),
            sa.Column("handoff_required", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("ticket_required", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("forbidden_commitments_json", sa.JSON(), nullable=True),
            sa.Column("allowed_resolution_actions_json", sa.JSON(), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("risk_key", "country_code", "channel", name="uq_escalation_policy_scope"),
        )
    for name, cols in {
        "ix_escalation_policies_risk_key": ["risk_key"],
        "ix_escalation_policies_country_code": ["country_code"],
        "ix_escalation_policies_channel": ["channel"],
        "ix_escalation_policies_enabled": ["enabled"],
    }.items():
        _create_index_once(name, "escalation_policies", cols)

    if "tool_execution_policies" not in tables:
        op.create_table(
            "tool_execution_policies",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("tool_name", sa.String(length=160), nullable=False),
            sa.Column("country_code", sa.String(length=16), nullable=False, server_default="GLOBAL"),
            sa.Column("channel", sa.String(length=40), nullable=False, server_default="all"),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("ai_auto_executable", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("risk_level", sa.String(length=40), nullable=False, server_default="low"),
            sa.Column("requires_tracking_number", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("requires_contact", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("requires_customer_confirmation", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("requires_human_confirmation", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("allowed_channels_json", sa.JSON(), nullable=True),
            sa.Column("allowed_countries_json", sa.JSON(), nullable=True),
            sa.Column("customer_visible_success_template", sa.Text(), nullable=True),
            sa.Column("customer_visible_failure_template", sa.Text(), nullable=True),
            sa.Column("audit_level", sa.String(length=80), nullable=False, server_default="standard"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("tool_name", "country_code", "channel", name="uq_tool_execution_policy_scope"),
        )
    for name, cols in {
        "ix_tool_execution_policies_tool_name": ["tool_name"],
        "ix_tool_execution_policies_country_code": ["country_code"],
        "ix_tool_execution_policies_channel": ["channel"],
        "ix_tool_execution_policies_enabled": ["enabled"],
        "ix_tool_execution_policies_ai_auto_executable": ["ai_auto_executable"],
        "ix_tool_execution_policies_risk_level": ["risk_level"],
    }.items():
        _create_index_once(name, "tool_execution_policies", cols)

    if "whatsapp_routing_rules" not in tables:
        op.create_table(
            "whatsapp_routing_rules",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("country_code", sa.String(length=16), nullable=False),
            sa.Column("issue_type", sa.String(length=120), nullable=False),
            sa.Column("channel", sa.String(length=40), nullable=False, server_default="whatsapp"),
            sa.Column("destination_group_id", sa.String(length=200), nullable=False),
            sa.Column("fallback_group_id", sa.String(length=200), nullable=True),
            sa.Column("working_hours_key", sa.String(length=120), nullable=True),
            sa.Column("message_template", sa.Text(), nullable=True),
            sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("country_code", "issue_type", "channel", name="uq_whatsapp_routing_scope"),
        )
    for name, cols in {
        "ix_whatsapp_routing_rules_country_code": ["country_code"],
        "ix_whatsapp_routing_rules_issue_type": ["issue_type"],
        "ix_whatsapp_routing_rules_channel": ["channel"],
        "ix_whatsapp_routing_rules_priority": ["priority"],
        "ix_whatsapp_routing_rules_enabled": ["enabled"],
    }.items():
        _create_index_once(name, "whatsapp_routing_rules", cols)

    if "runtime_decision_audits" not in tables:
        op.create_table(
            "runtime_decision_audits",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("tenant_id", sa.String(length=80), nullable=False, server_default="default"),
            sa.Column("channel", sa.String(length=40), nullable=True),
            sa.Column("country_code", sa.String(length=16), nullable=True),
            sa.Column("conversation_id", sa.Integer(), nullable=True),
            sa.Column("ticket_id", sa.Integer(), nullable=True),
            sa.Column("business_reply_type", sa.String(length=120), nullable=False),
            sa.Column("next_action", sa.String(length=120), nullable=False),
            sa.Column("risk_level", sa.String(length=40), nullable=False, server_default="low"),
            sa.Column("allowed", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("violations_json", sa.JSON(), nullable=True),
            sa.Column("warnings_json", sa.JSON(), nullable=True),
            sa.Column("decision_json", sa.JSON(), nullable=False),
            sa.Column("case_context_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["conversation_id"], ["webchat_conversations.id"]),
            sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
        )
    for name, cols in {
        "ix_runtime_decision_audits_tenant_id": ["tenant_id"],
        "ix_runtime_decision_audits_channel": ["channel"],
        "ix_runtime_decision_audits_country_code": ["country_code"],
        "ix_runtime_decision_audits_conversation_id": ["conversation_id"],
        "ix_runtime_decision_audits_ticket_id": ["ticket_id"],
        "ix_runtime_decision_audits_business_reply_type": ["business_reply_type"],
        "ix_runtime_decision_audits_next_action": ["next_action"],
        "ix_runtime_decision_audits_risk_level": ["risk_level"],
        "ix_runtime_decision_audits_allowed": ["allowed"],
        "ix_runtime_decision_audits_created_at": ["created_at"],
    }.items():
        _create_index_once(name, "runtime_decision_audits", cols)


def downgrade() -> None:
    bind = op.get_bind()
    for table_name in [
        "runtime_decision_audits",
        "whatsapp_routing_rules",
        "tool_execution_policies",
        "escalation_policies",
        "human_hours_policies",
        "case_contexts",
    ]:
        if table_name in _tables(bind):
            op.drop_table(table_name)
