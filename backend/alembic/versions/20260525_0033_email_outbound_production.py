"""email outbound production foundation

Revision ID: 20260525_0033_email_outbound_production
Revises: 20260523_0032
Create Date: 2026-05-25
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260525_0033_email_outbound_production"
down_revision = "20260523_0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_channel_accounts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("channel_account_id", sa.Integer(), nullable=False),
        sa.Column("from_email", sa.String(length=320), nullable=False),
        sa.Column("from_name", sa.String(length=160), nullable=True),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("region", sa.String(length=80), nullable=True),
        sa.Column("configuration_set", sa.String(length=160), nullable=True),
        sa.Column("verification_status", sa.String(length=40), nullable=False),
        sa.Column("inbound_domain", sa.String(length=255), nullable=True),
        sa.Column("plus_address_tag", sa.String(length=80), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_test_send_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_readiness_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["channel_account_id"], ["channel_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("channel_account_id", name="uq_email_channel_account_channel_account"),
    )
    op.create_index(op.f("ix_email_channel_accounts_from_email"), "email_channel_accounts", ["from_email"], unique=True)
    op.create_index(op.f("ix_email_channel_accounts_is_active"), "email_channel_accounts", ["is_active"])
    op.create_index(op.f("ix_email_channel_accounts_plus_address_tag"), "email_channel_accounts", ["plus_address_tag"])
    op.create_index(op.f("ix_email_channel_accounts_provider"), "email_channel_accounts", ["provider"])
    op.create_index(op.f("ix_email_channel_accounts_verification_status"), "email_channel_accounts", ["verification_status"])

    op.create_table(
        "email_outbound_metadata",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("outbound_message_id", sa.Integer(), nullable=False),
        sa.Column("email_account_id", sa.Integer(), nullable=True),
        sa.Column("from_email", sa.String(length=320), nullable=False),
        sa.Column("to_email", sa.String(length=320), nullable=False),
        sa.Column("cc_json", sa.JSON(), nullable=True),
        sa.Column("bcc_json", sa.JSON(), nullable=True),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("provider_thread_id", sa.String(length=255), nullable=True),
        sa.Column("reply_token", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["email_account_id"], ["email_channel_accounts.id"]),
        sa.ForeignKeyConstraint(["outbound_message_id"], ["ticket_outbound_messages.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("outbound_message_id", name="uq_email_outbound_metadata_message"),
    )
    op.create_index(op.f("ix_email_outbound_metadata_provider_message_id"), "email_outbound_metadata", ["provider_message_id"])
    op.create_index(op.f("ix_email_outbound_metadata_provider_thread_id"), "email_outbound_metadata", ["provider_thread_id"])
    op.create_index(op.f("ix_email_outbound_metadata_reply_token"), "email_outbound_metadata", ["reply_token"], unique=True)
    op.create_index(op.f("ix_email_outbound_metadata_to_email"), "email_outbound_metadata", ["to_email"])

    op.create_table(
        "email_delivery_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("outbound_message_id", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("provider_event_id", sa.String(length=255), nullable=False),
        sa.Column("provider_message_id", sa.String(length=255), nullable=True),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("recipient", sa.String(length=320), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["outbound_message_id"], ["ticket_outbound_messages.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "provider_event_id", name="uq_email_delivery_provider_event"),
    )
    op.create_index(op.f("ix_email_delivery_events_event_type"), "email_delivery_events", ["event_type"])
    op.create_index(op.f("ix_email_delivery_events_provider_message_id"), "email_delivery_events", ["provider_message_id"])
    op.create_index(op.f("ix_email_delivery_events_recipient"), "email_delivery_events", ["recipient"])

    op.create_table(
        "email_suppressions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("email_normalized", sa.String(length=320), nullable=False),
        sa.Column("reason", sa.String(length=80), nullable=False),
        sa.Column("source_event_id", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["source_event_id"], ["email_delivery_events.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email_normalized", name="uq_email_suppression_email"),
    )
    op.create_index(op.f("ix_email_suppressions_email_normalized"), "email_suppressions", ["email_normalized"])
    op.create_index(op.f("ix_email_suppressions_is_active"), "email_suppressions", ["is_active"])
    op.create_index(op.f("ix_email_suppressions_reason"), "email_suppressions", ["reason"])

    op.create_table(
        "email_inbound_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticket_id", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("provider_message_id", sa.String(length=255), nullable=False),
        sa.Column("from_email", sa.String(length=320), nullable=False),
        sa.Column("to_email", sa.String(length=320), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("reply_token", sa.String(length=80), nullable=True),
        sa.Column("link_status", sa.String(length=40), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "provider_message_id", name="uq_email_inbound_provider_message"),
    )
    op.create_index(op.f("ix_email_inbound_messages_reply_token"), "email_inbound_messages", ["reply_token"])
    op.create_index(op.f("ix_email_inbound_messages_link_status"), "email_inbound_messages", ["link_status"])

    op.create_table(
        "email_webhook_replays",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("signature", sa.String(length=128), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("signature", name="uq_email_webhook_replay_signature"),
    )
    op.create_index(op.f("ix_email_webhook_replays_timestamp"), "email_webhook_replays", ["timestamp"])


def downgrade() -> None:
    op.drop_table("email_webhook_replays")
    op.drop_table("email_inbound_messages")
    op.drop_table("email_suppressions")
    op.drop_table("email_delivery_events")
    op.drop_table("email_outbound_metadata")
    op.drop_table("email_channel_accounts")
