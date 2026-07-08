"""webchat ai debug observability

Revision ID: 20260708_0053
Revises: 20260707_0052
Create Date: 2026-07-08
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260708_0053"
down_revision = "20260707_0052"
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

    if "webchat_ai_debug_runs" not in tables:
        op.create_table(
            "webchat_ai_debug_runs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("conversation_id", sa.Integer(), nullable=False),
            sa.Column("ticket_id", sa.Integer(), nullable=False),
            sa.Column("ai_turn_id", sa.Integer(), nullable=False),
            sa.Column("visitor_message_id", sa.Integer(), nullable=True),
            sa.Column("reply_message_id", sa.Integer(), nullable=True),
            sa.Column("request_id", sa.String(length=160), nullable=True),
            sa.Column("channel", sa.String(length=40), nullable=True),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="unknown"),
            sa.Column("status_reason", sa.Text(), nullable=True),
            sa.Column("intent", sa.String(length=80), nullable=True),
            sa.Column("reply_type", sa.String(length=80), nullable=True),
            sa.Column("reply_source", sa.String(length=80), nullable=True),
            sa.Column("provider_status", sa.String(length=120), nullable=True),
            sa.Column("tracking_intent_detected", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("tracking_fact_evidence_present", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("tool_facts_present", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("live_tracking_answer_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("kb_hits_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("tool_call_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("runtime_event_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("prior_ai_messages_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("customer_claim_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("memory_system", sa.String(length=80), nullable=False, server_default="unknown"),
            sa.Column("support_memory_ledger_used_by_runtime", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("safety_status", sa.String(length=80), nullable=True),
            sa.Column("fact_gate_reason", sa.Text(), nullable=True),
            sa.Column("customer_visible_message_created", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("debug_bundle_json", sa.Text(), nullable=False),
            sa.Column("privacy_report_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["conversation_id"], ["webchat_conversations.id"]),
            sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
            sa.ForeignKeyConstraint(["ai_turn_id"], ["webchat_ai_turns.id"]),
            sa.ForeignKeyConstraint(["visitor_message_id"], ["webchat_messages.id"]),
            sa.ForeignKeyConstraint(["reply_message_id"], ["webchat_messages.id"]),
            sa.UniqueConstraint("ai_turn_id", name="uq_webchat_ai_debug_runs_ai_turn_id"),
        )

    for name, cols in {
        "ix_webchat_ai_debug_runs_conversation_id": ["conversation_id"],
        "ix_webchat_ai_debug_runs_ticket_id": ["ticket_id"],
        "ix_webchat_ai_debug_runs_ai_turn_id": ["ai_turn_id"],
        "ix_webchat_ai_debug_runs_visitor_message_id": ["visitor_message_id"],
        "ix_webchat_ai_debug_runs_reply_message_id": ["reply_message_id"],
        "ix_webchat_ai_debug_runs_request_id": ["request_id"],
        "ix_webchat_ai_debug_runs_channel": ["channel"],
        "ix_webchat_ai_debug_runs_status": ["status"],
        "ix_webchat_ai_debug_runs_intent": ["intent"],
        "ix_webchat_ai_debug_runs_reply_type": ["reply_type"],
        "ix_webchat_ai_debug_runs_reply_source": ["reply_source"],
        "ix_webchat_ai_debug_runs_provider_status": ["provider_status"],
        "ix_webchat_ai_debug_runs_tracking_intent_detected": ["tracking_intent_detected"],
        "ix_webchat_ai_debug_runs_tracking_fact_evidence_present": ["tracking_fact_evidence_present"],
        "ix_webchat_ai_debug_runs_tool_facts_present": ["tool_facts_present"],
        "ix_webchat_ai_debug_runs_live_tracking_answer_allowed": ["live_tracking_answer_allowed"],
        "ix_webchat_ai_debug_runs_kb_hits_count": ["kb_hits_count"],
        "ix_webchat_ai_debug_runs_memory_system": ["memory_system"],
        "ix_webchat_ai_debug_runs_safety_status": ["safety_status"],
        "ix_webchat_ai_debug_runs_customer_visible_message_created": ["customer_visible_message_created"],
        "ix_webchat_ai_debug_runs_created_at": ["created_at"],
        "ix_webchat_ai_debug_runs_updated_at": ["updated_at"],
        "ix_webchat_ai_debug_runs_completed_at": ["completed_at"],
        "ix_webchat_ai_debug_runs_ticket_created": ["ticket_id", "created_at"],
    }.items():
        _create_index_once(name, "webchat_ai_debug_runs", cols)

    if "webchat_ai_test_findings" not in _tables(bind):
        op.create_table(
            "webchat_ai_test_findings",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("debug_run_id", sa.Integer(), nullable=False),
            sa.Column("ai_turn_id", sa.Integer(), nullable=False),
            sa.Column("conversation_id", sa.Integer(), nullable=False),
            sa.Column("ticket_id", sa.Integer(), nullable=False),
            sa.Column("finding_type", sa.String(length=120), nullable=False),
            sa.Column("severity", sa.String(length=40), nullable=False, server_default="medium"),
            sa.Column("tester_note", sa.Text(), nullable=True),
            sa.Column("expected_behavior", sa.Text(), nullable=True),
            sa.Column("actual_behavior", sa.Text(), nullable=True),
            sa.Column("bundle_snapshot_json", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="open"),
            sa.Column("linked_issue_url", sa.String(length=500), nullable=True),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["debug_run_id"], ["webchat_ai_debug_runs.id"]),
            sa.ForeignKeyConstraint(["ai_turn_id"], ["webchat_ai_turns.id"]),
            sa.ForeignKeyConstraint(["conversation_id"], ["webchat_conversations.id"]),
            sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        )

    for name, cols in {
        "ix_webchat_ai_test_findings_debug_run_id": ["debug_run_id"],
        "ix_webchat_ai_test_findings_ai_turn_id": ["ai_turn_id"],
        "ix_webchat_ai_test_findings_conversation_id": ["conversation_id"],
        "ix_webchat_ai_test_findings_ticket_id": ["ticket_id"],
        "ix_webchat_ai_test_findings_finding_type": ["finding_type"],
        "ix_webchat_ai_test_findings_severity": ["severity"],
        "ix_webchat_ai_test_findings_status": ["status"],
        "ix_webchat_ai_test_findings_created_by": ["created_by"],
        "ix_webchat_ai_test_findings_created_at": ["created_at"],
    }.items():
        _create_index_once(name, "webchat_ai_test_findings", cols)

    if "webchat_ai_eval_cases" not in _tables(bind):
        op.create_table(
            "webchat_ai_eval_cases",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("case_key", sa.String(length=200), nullable=False),
            sa.Column("source_debug_run_id", sa.Integer(), nullable=False),
            sa.Column("source_finding_id", sa.Integer(), nullable=True),
            sa.Column("scenario", sa.Text(), nullable=False),
            sa.Column("intent", sa.String(length=80), nullable=True),
            sa.Column("channel", sa.String(length=40), nullable=True),
            sa.Column("language", sa.String(length=16), nullable=True),
            sa.Column("input_redacted_summary", sa.Text(), nullable=True),
            sa.Column("expected_policy_json", sa.Text(), nullable=False),
            sa.Column("expected_reply_type", sa.String(length=80), nullable=True),
            sa.Column("required_evidence_json", sa.Text(), nullable=False),
            sa.Column("forbidden_sources_json", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="active"),
            sa.Column("created_by", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["source_debug_run_id"], ["webchat_ai_debug_runs.id"]),
            sa.ForeignKeyConstraint(["source_finding_id"], ["webchat_ai_test_findings.id"]),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
            sa.UniqueConstraint("case_key", name="uq_webchat_ai_eval_cases_case_key"),
        )

    for name, cols in {
        "ix_webchat_ai_eval_cases_case_key": ["case_key"],
        "ix_webchat_ai_eval_cases_source_debug_run_id": ["source_debug_run_id"],
        "ix_webchat_ai_eval_cases_source_finding_id": ["source_finding_id"],
        "ix_webchat_ai_eval_cases_intent": ["intent"],
        "ix_webchat_ai_eval_cases_channel": ["channel"],
        "ix_webchat_ai_eval_cases_language": ["language"],
        "ix_webchat_ai_eval_cases_expected_reply_type": ["expected_reply_type"],
        "ix_webchat_ai_eval_cases_status": ["status"],
        "ix_webchat_ai_eval_cases_created_by": ["created_by"],
        "ix_webchat_ai_eval_cases_created_at": ["created_at"],
    }.items():
        _create_index_once(name, "webchat_ai_eval_cases", cols)


def downgrade() -> None:
    bind = op.get_bind()
    for table_name in ["webchat_ai_eval_cases", "webchat_ai_test_findings", "webchat_ai_debug_runs"]:
        if table_name in _tables(bind):
            op.drop_table(table_name)
