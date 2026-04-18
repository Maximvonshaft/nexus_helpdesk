"""initial stable production baseline

Revision ID: 20260410_0001
Revises: 
Create Date: 2026-04-10 00:01:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260410_0001"
down_revision = None
branch_labels = None
depends_on = None


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


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

    if "auth_throttle_entries" not in tables:
        op.create_table(
            "auth_throttle_entries",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("throttle_key", sa.String(length=255), nullable=False),
            sa.Column("fail_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_failed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("throttle_key", name="uq_auth_throttle_entries_throttle_key"),
        )
        tables = _tables(bind)

    if "customers" not in tables:
        op.create_table(
            "customers",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=160), nullable=False),
            sa.Column("email", sa.String(length=200), nullable=True),
            sa.Column("email_normalized", sa.String(length=200), nullable=True),
            sa.Column("phone", sa.String(length=60), nullable=True),
            sa.Column("phone_normalized", sa.String(length=60), nullable=True),
            sa.Column("external_ref", sa.String(length=120), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        tables = _tables(bind)

    if "integration_clients" not in tables:
        op.create_table(
            "integration_clients",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("key_id", sa.String(length=120), nullable=False),
            sa.Column("secret_hash", sa.String(length=255), nullable=False),
            sa.Column("scopes_csv", sa.Text(), nullable=False, server_default="profile.read,task.write"),
            sa.Column("rate_limit_per_minute", sa.Integer(), nullable=False, server_default="60"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("name", name="uq_integration_clients_name"),
            sa.UniqueConstraint("key_id", name="uq_integration_clients_key_id"),
        )
        tables = _tables(bind)

    if "integration_request_logs" not in tables:
        op.create_table(
            "integration_request_logs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("client_id", sa.Integer(), sa.ForeignKey("integration_clients.id"), nullable=True),
            sa.Column("endpoint", sa.String(length=160), nullable=False),
            sa.Column("method", sa.String(length=16), nullable=False, server_default="GET"),
            sa.Column("idempotency_key", sa.String(length=160), nullable=True),
            sa.Column("request_hash", sa.String(length=64), nullable=True),
            sa.Column("response_json", sa.Text(), nullable=True),
            sa.Column("status_code", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("client_id", "endpoint", "idempotency_key", name="uq_integration_client_endpoint_idem"),
        )
        tables = _tables(bind)

    if "sla_policies" not in tables:
        op.create_table(
            "sla_policies",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("priority", sa.String(length=32), nullable=False),
            sa.Column("first_response_minutes", sa.Integer(), nullable=False),
            sa.Column("resolution_minutes", sa.Integer(), nullable=False),
            sa.Column("pause_on_waiting_customer", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("pause_on_waiting_internal", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("name", name="uq_sla_policies_name"),
            sa.UniqueConstraint("priority", name="uq_sla_policies_priority"),
        )
        tables = _tables(bind)

    if "tags" not in tables:
        op.create_table(
            "tags",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=60), nullable=False),
            sa.Column("color", sa.String(length=30), nullable=True),
            sa.UniqueConstraint("name", name="uq_tags_name"),
        )
        tables = _tables(bind)

    if "teams" not in tables:
        op.create_table(
            "teams",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("team_type", sa.String(length=80), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("name", name="uq_teams_name"),
        )
        tables = _tables(bind)

    if "users" not in tables:
        op.create_table(
            "users",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("username", sa.String(length=80), nullable=False),
            sa.Column("display_name", sa.String(length=120), nullable=False),
            sa.Column("email", sa.String(length=200), nullable=True),
            sa.Column("password_hash", sa.String(length=255), nullable=False),
            sa.Column("role", sa.String(length=32), nullable=False),
            sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("username", name="uq_users_username"),
            sa.UniqueConstraint("email", name="uq_users_email"),
        )
        tables = _tables(bind)

    if "tickets" not in tables:
        op.create_table(
            "tickets",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("ticket_no", sa.String(length=40), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=True),
            sa.Column("source", sa.String(length=32), nullable=False),
            sa.Column("source_channel", sa.String(length=32), nullable=False),
            sa.Column("priority", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("category", sa.String(length=80), nullable=True),
            sa.Column("sub_category", sa.String(length=80), nullable=True),
            sa.Column("tracking_number", sa.String(length=120), nullable=True),
            sa.Column("assignee_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=True),
            sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("sla_policy_id", sa.Integer(), sa.ForeignKey("sla_policies.id"), nullable=True),
            sa.Column("first_response_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("first_response_due_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("resolution_due_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("reopen_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("resolution_category", sa.String(length=32), nullable=False, server_default="none"),
            sa.Column("sla_paused", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("sla_paused_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("sla_pause_reason", sa.String(length=120), nullable=True),
            sa.Column("total_paused_seconds", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("first_response_breached", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("resolution_breached", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("ai_summary", sa.Text(), nullable=True),
            sa.Column("ai_classification", sa.String(length=120), nullable=True),
            sa.Column("ai_confidence", sa.Float(), nullable=True),
            sa.Column("case_type", sa.String(length=120), nullable=True),
            sa.Column("issue_summary", sa.Text(), nullable=True),
            sa.Column("customer_request", sa.Text(), nullable=True),
            sa.Column("source_chat_id", sa.String(length=120), nullable=True),
            sa.Column("required_action", sa.Text(), nullable=True),
            sa.Column("missing_fields", sa.Text(), nullable=True),
            sa.Column("last_customer_message", sa.Text(), nullable=True),
            sa.Column("customer_update", sa.Text(), nullable=True),
            sa.Column("resolution_summary", sa.Text(), nullable=True),
            sa.Column("last_human_update", sa.Text(), nullable=True),
            sa.Column("requested_time", sa.String(length=120), nullable=True),
            sa.Column("destination", sa.String(length=160), nullable=True),
            sa.Column("preferred_reply_channel", sa.String(length=60), nullable=True),
            sa.Column("preferred_reply_contact", sa.String(length=160), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("ticket_no", name="uq_tickets_ticket_no"),
        )
        tables = _tables(bind)

    if "ticket_attachments" not in tables:
        op.create_table(
            "ticket_attachments",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id"), nullable=False),
            sa.Column("uploaded_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("file_name", sa.String(length=255), nullable=False),
            sa.Column("storage_key", sa.String(length=255), nullable=True),
            sa.Column("file_path", sa.String(length=500), nullable=True),
            sa.Column("file_url", sa.String(length=500), nullable=True),
            sa.Column("mime_type", sa.String(length=120), nullable=True),
            sa.Column("file_size", sa.Integer(), nullable=True),
            sa.Column("visibility", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        tables = _tables(bind)

    if "ticket_comments" not in tables:
        op.create_table(
            "ticket_comments",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id"), nullable=False),
            sa.Column("author_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("visibility", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        tables = _tables(bind)

    if "ticket_events" not in tables:
        op.create_table(
            "ticket_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id"), nullable=False),
            sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("event_type", sa.String(length=120), nullable=False),
            sa.Column("field_name", sa.String(length=120), nullable=True),
            sa.Column("old_value", sa.Text(), nullable=True),
            sa.Column("new_value", sa.Text(), nullable=True),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("payload_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        tables = _tables(bind)

    if "ticket_followers" not in tables:
        op.create_table(
            "ticket_followers",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id"), nullable=False),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.UniqueConstraint("ticket_id", "user_id", name="uq_ticket_follower"),
        )
        tables = _tables(bind)

    if "ticket_internal_notes" not in tables:
        op.create_table(
            "ticket_internal_notes",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id"), nullable=False),
            sa.Column("author_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        tables = _tables(bind)

    if "ticket_outbound_messages" not in tables:
        op.create_table(
            "ticket_outbound_messages",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id"), nullable=False),
            sa.Column("channel", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("provider_status", sa.String(length=120), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("max_retries", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("locked_by", sa.String(length=120), nullable=True),
            sa.Column("provider_message_id", sa.String(length=255), nullable=True),
            sa.Column("failure_code", sa.String(length=120), nullable=True),
            sa.Column("failure_reason", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        tables = _tables(bind)

    if "ticket_tags" not in tables:
        op.create_table(
            "ticket_tags",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id"), nullable=False),
            sa.Column("tag_id", sa.Integer(), sa.ForeignKey("tags.id"), nullable=False),
            sa.UniqueConstraint("ticket_id", "tag_id", name="uq_ticket_tag"),
        )
        tables = _tables(bind)

    if "background_jobs" not in tables:
        op.create_table(
            "background_jobs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("queue_name", sa.String(length=80), nullable=False),
            sa.Column("job_type", sa.String(length=120), nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("locked_by", sa.String(length=120), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )

    # Core indexes kept in the baseline; later revisions add operational/reconciliation indexes.
    for name, table_name, columns, unique in [
        ("ix_auth_throttle_entries_locked_until", "auth_throttle_entries", ["locked_until"], False),
        ("ix_auth_throttle_entries_throttle_key", "auth_throttle_entries", ["throttle_key"], True),
        ("ix_background_jobs_created_at", "background_jobs", ["created_at"], False),
        ("ix_background_jobs_job_type", "background_jobs", ["job_type"], False),
        ("ix_background_jobs_locked_at", "background_jobs", ["locked_at"], False),
        ("ix_background_jobs_locked_by", "background_jobs", ["locked_by"], False),
        ("ix_background_jobs_next_run_at", "background_jobs", ["next_run_at"], False),
        ("ix_background_jobs_queue_name", "background_jobs", ["queue_name"], False),
        ("ix_background_jobs_status", "background_jobs", ["status"], False),
        ("ix_customers_email", "customers", ["email"], False),
        ("ix_customers_email_normalized", "customers", ["email_normalized"], False),
        ("ix_customers_external_ref", "customers", ["external_ref"], False),
        ("ix_customers_name", "customers", ["name"], False),
        ("ix_customers_phone", "customers", ["phone"], False),
        ("ix_customers_phone_normalized", "customers", ["phone_normalized"], False),
        ("ix_integration_clients_key_id", "integration_clients", ["key_id"], True),
        ("ix_integration_clients_name", "integration_clients", ["name"], True),
        ("ix_integration_request_logs_client_id", "integration_request_logs", ["client_id"], False),
        ("ix_integration_request_logs_created_at", "integration_request_logs", ["created_at"], False),
        ("ix_integration_request_logs_endpoint", "integration_request_logs", ["endpoint"], False),
        ("ix_integration_request_logs_idempotency_key", "integration_request_logs", ["idempotency_key"], False),
        ("ix_integration_request_logs_request_hash", "integration_request_logs", ["request_hash"], False),
        ("ix_sla_policies_priority", "sla_policies", ["priority"], True),
        ("ix_tags_name", "tags", ["name"], True),
        ("ix_teams_name", "teams", ["name"], True),
        ("ix_users_role", "users", ["role"], False),
        ("ix_users_team_id", "users", ["team_id"], False),
        ("ix_users_username", "users", ["username"], True),
        ("ix_tickets_assignee_id", "tickets", ["assignee_id"], False),
        ("ix_tickets_case_type", "tickets", ["case_type"], False),
        ("ix_tickets_category", "tickets", ["category"], False),
        ("ix_tickets_created_at", "tickets", ["created_at"], False),
        ("ix_tickets_created_by", "tickets", ["created_by"], False),
        ("ix_tickets_customer_id", "tickets", ["customer_id"], False),
        ("ix_tickets_first_response_due_at", "tickets", ["first_response_due_at"], False),
        ("ix_tickets_priority", "tickets", ["priority"], False),
        ("ix_tickets_resolution_category", "tickets", ["resolution_category"], False),
        ("ix_tickets_resolution_due_at", "tickets", ["resolution_due_at"], False),
        ("ix_tickets_source", "tickets", ["source"], False),
        ("ix_tickets_source_channel", "tickets", ["source_channel"], False),
        ("ix_tickets_source_chat_id", "tickets", ["source_chat_id"], False),
        ("ix_tickets_status", "tickets", ["status"], False),
        ("ix_tickets_sub_category", "tickets", ["sub_category"], False),
        ("ix_tickets_team_id", "tickets", ["team_id"], False),
        ("ix_tickets_ticket_no", "tickets", ["ticket_no"], True),
        ("ix_tickets_title", "tickets", ["title"], False),
        ("ix_tickets_tracking_number", "tickets", ["tracking_number"], False),
        ("ix_tickets_updated_at", "tickets", ["updated_at"], False),
        ("ix_ticket_attachments_created_at", "ticket_attachments", ["created_at"], False),
        ("ix_ticket_attachments_storage_key", "ticket_attachments", ["storage_key"], False),
        ("ix_ticket_attachments_ticket_id", "ticket_attachments", ["ticket_id"], False),
        ("ix_ticket_attachments_uploaded_by", "ticket_attachments", ["uploaded_by"], False),
        ("ix_ticket_comments_author_id", "ticket_comments", ["author_id"], False),
        ("ix_ticket_comments_created_at", "ticket_comments", ["created_at"], False),
        ("ix_ticket_comments_ticket_id", "ticket_comments", ["ticket_id"], False),
        ("ix_ticket_events_actor_id", "ticket_events", ["actor_id"], False),
        ("ix_ticket_events_created_at", "ticket_events", ["created_at"], False),
        ("ix_ticket_events_event_type", "ticket_events", ["event_type"], False),
        ("ix_ticket_events_field_name", "ticket_events", ["field_name"], False),
        ("ix_ticket_events_ticket_id", "ticket_events", ["ticket_id"], False),
        ("ix_ticket_followers_ticket_id", "ticket_followers", ["ticket_id"], False),
        ("ix_ticket_followers_user_id", "ticket_followers", ["user_id"], False),
        ("ix_ticket_internal_notes_author_id", "ticket_internal_notes", ["author_id"], False),
        ("ix_ticket_internal_notes_created_at", "ticket_internal_notes", ["created_at"], False),
        ("ix_ticket_internal_notes_ticket_id", "ticket_internal_notes", ["ticket_id"], False),
        ("ix_ticket_outbound_messages_channel", "ticket_outbound_messages", ["channel"], False),
        ("ix_ticket_outbound_messages_created_at", "ticket_outbound_messages", ["created_at"], False),
        ("ix_ticket_outbound_messages_created_by", "ticket_outbound_messages", ["created_by"], False),
        ("ix_ticket_outbound_messages_locked_at", "ticket_outbound_messages", ["locked_at"], False),
        ("ix_ticket_outbound_messages_locked_by", "ticket_outbound_messages", ["locked_by"], False),
        ("ix_ticket_outbound_messages_next_retry_at", "ticket_outbound_messages", ["next_retry_at"], False),
        ("ix_ticket_outbound_messages_sent_at", "ticket_outbound_messages", ["sent_at"], False),
        ("ix_ticket_outbound_messages_status", "ticket_outbound_messages", ["status"], False),
        ("ix_ticket_outbound_messages_ticket_id", "ticket_outbound_messages", ["ticket_id"], False),
        ("ix_ticket_tags_tag_id", "ticket_tags", ["tag_id"], False),
        ("ix_ticket_tags_ticket_id", "ticket_tags", ["ticket_id"], False),
    ]:
        _create_index_if_missing(bind, name, table_name, columns, unique=unique)


def downgrade() -> None:
    bind = op.get_bind()
    for table_name in [
        "background_jobs",
        "ticket_tags",
        "ticket_outbound_messages",
        "ticket_internal_notes",
        "ticket_followers",
        "ticket_events",
        "ticket_comments",
        "ticket_attachments",
        "tickets",
        "users",
        "teams",
        "tags",
        "sla_policies",
        "integration_request_logs",
        "integration_clients",
        "customers",
        "auth_throttle_entries",
    ]:
        if table_name in _tables(bind):
            op.drop_table(table_name)
