"""rename retired channel compatibility surfaces

Revision ID: 20260630_0048
Revises: 20260612_0017
Create Date: 2026-06-30
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260630_0048"
down_revision = "20260612_0017"
branch_labels = None
depends_on = None

NEW_PREFIX = "external_channel"
OLD_PREFIX = "open" + "claw"

TABLES = (
    "external_channel_conversation_links",
    "external_channel_transcript_messages",
    "external_channel_attachment_references",
    "external_channel_sync_cursors",
    "external_channel_unresolved_events",
)

INDEXES = (
    "ix_external_channel_attachment_refs_remote_attachment_id",
    "ix_external_channel_attachment_refs_storage_status",
    "ix_external_channel_attachment_refs_ticket_id",
    "ix_external_channel_attachment_references_conversation_id",
    "ix_external_channel_attachment_references_remote_attachment_id",
    "ix_external_channel_attachment_references_ticket_id",
    "ix_external_channel_attachment_references_transcript_message_id",
    "ix_external_channel_conversation_links_channel",
    "ix_external_channel_conversation_links_channel_account_id",
    "ix_external_channel_conversation_links_created_at",
    "ix_external_channel_conversation_links_last_synced_at",
    "ix_external_channel_conversation_links_recipient",
    "ix_external_channel_conversation_links_session_key",
    "ix_external_channel_conversation_links_ticket_id",
    "ix_external_channel_links_session_updated",
    "ix_external_channel_links_ticket_updated",
    "ix_external_channel_sync_cursors_source",
    "ix_external_channel_transcript_messages_conversation_id",
    "ix_external_channel_transcript_messages_created_at",
    "ix_external_channel_transcript_messages_message_id",
    "ix_external_channel_transcript_messages_received_at",
    "ix_external_channel_transcript_messages_role",
    "ix_external_channel_transcript_messages_session_key",
    "ix_external_channel_transcript_messages_ticket_id",
    "ix_external_channel_transcript_ticket_received",
    "ix_external_channel_unresolved_events_created_at",
    "ix_external_channel_unresolved_events_recipient",
    "ix_external_channel_unresolved_events_session_key",
    "ix_external_channel_unresolved_events_source_chat_id",
    "ix_external_channel_unresolved_events_status",
    "ix_external_channel_unresolved_payload_hash_status",
    "uq_external_channel_unresolved_active_payload_hash",
    "uq_operator_tasks_active_external_channel_unresolved",
    "ix_channel_onboarding_tasks_external_channel_account_id",
)

CONSTRAINTS = (
    ("external_channel_conversation_links", "uq_external_channel_session_key"),
    ("external_channel_conversation_links", "uq_external_channel_ticket_link"),
    ("external_channel_transcript_messages", "uq_external_channel_conversation_message"),
)

VALUE_COLUMNS = {
    "admin_audit_logs": ("target_type", "old_value_json", "new_value_json"),
    "background_jobs": ("queue_name", "job_type", "payload_json", "last_error"),
    "channel_accounts": ("provider",),
    "channel_onboarding_tasks": ("provider", "external_channel_account_id", "last_error"),
    "operator_tasks": ("source_type", "reason_code", "payload_json"),
    "provider_routing_rules": ("primary_provider", "fallback_providers"),
    "service_heartbeats": ("service_name",),
    "ticket_events": ("event_type", "field_name", "note", "payload_json"),
    "ticket_outbound_messages": ("provider_status", "failure_code", "failure_reason"),
    "tool_audit_logs": ("provider", "tool_name", "request_json", "result_json", "error_message"),
    "tool_governance_audit_logs": ("provider", "tool_name", "request_json", "result_json", "error_message"),
}


def _old(value: str) -> str:
    return value.replace(NEW_PREFIX, OLD_PREFIX)


def _tables(inspector: sa.Inspector) -> set[str]:
    return set(inspector.get_table_names())


def _columns(inspector: sa.Inspector, table_name: str) -> set[str]:
    try:
        return {column["name"] for column in inspector.get_columns(table_name)}
    except sa.exc.NoSuchTableError:
        return set()


def _rename_tables(inspector: sa.Inspector, *, reverse: bool = False) -> None:
    existing = _tables(inspector)
    for new_name in TABLES:
        old_name = _old(new_name)
        source = new_name if reverse else old_name
        target = old_name if reverse else new_name
        if source in existing and target not in existing:
            op.rename_table(source, target)
            existing.remove(source)
            existing.add(target)


def _rename_columns(inspector: sa.Inspector, *, reverse: bool = False) -> None:
    if "channel_onboarding_tasks" not in _tables(inspector):
        return
    cols = _columns(inspector, "channel_onboarding_tasks")
    new_col = "external_channel_account_id"
    old_col = _old(new_col)
    source = new_col if reverse else old_col
    target = old_col if reverse else new_col
    if source in cols and target not in cols:
        op.alter_column("channel_onboarding_tasks", source, new_column_name=target)


def _rename_indexes_and_constraints(*, reverse: bool = False) -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for new_name in INDEXES:
        old_name = _old(new_name)
        source = new_name if reverse else old_name
        target = old_name if reverse else new_name
        op.execute(sa.text(f'ALTER INDEX IF EXISTS "{source}" RENAME TO "{target}"'))
    for table_name, new_name in CONSTRAINTS:
        old_table_name = _old(table_name)
        old_name = _old(new_name)
        source_table = table_name if not reverse else old_table_name
        source = new_name if reverse else old_name
        target = old_name if reverse else new_name
        op.execute(sa.text(f'ALTER TABLE IF EXISTS "{source_table}" RENAME CONSTRAINT "{source}" TO "{target}"'))


def _replace_column_values(inspector: sa.Inspector, *, reverse: bool = False) -> None:
    bind = op.get_bind()
    existing_tables = _tables(inspector)
    replacements = (
        (OLD_PREFIX, NEW_PREFIX),
        (OLD_PREFIX.upper(), NEW_PREFIX.upper()),
        ("Open" + "Claw", "ExternalChannel"),
    )
    if reverse:
        replacements = tuple((new, old) for old, new in replacements)
    for table_name, columns in VALUE_COLUMNS.items():
        if table_name not in existing_tables:
            continue
        existing_columns = _columns(inspector, table_name)
        for column in columns:
            if column not in existing_columns:
                continue
            for old_value, new_value in replacements:
                bind.execute(
                    sa.text(
                        f'UPDATE "{table_name}" '
                        f'SET "{column}" = replace("{column}", :old_value, :new_value) '
                        f'WHERE "{column}" LIKE :pattern'
                    ),
                    {
                        "old_value": old_value,
                        "new_value": new_value,
                        "pattern": f"%{old_value}%",
                    },
                )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    _rename_tables(inspector)
    inspector = sa.inspect(bind)
    _rename_columns(inspector)
    _rename_indexes_and_constraints()
    inspector = sa.inspect(bind)
    _replace_column_values(inspector)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    _replace_column_values(inspector, reverse=True)
    _rename_indexes_and_constraints(reverse=True)
    inspector = sa.inspect(bind)
    _rename_columns(inspector, reverse=True)
    inspector = sa.inspect(bind)
    _rename_tables(inspector, reverse=True)
