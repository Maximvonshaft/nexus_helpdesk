"""control plane overlay round 1

Revision ID: 20260422_ctrl_r1
Revises: 20260421_governance_overlay_round4
Create Date: 2026-04-22
"""
from alembic import op
import sqlalchemy as sa

revision = "20260422_ctrl_r1"
down_revision = "20260410_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "persona_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("profile_key", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("market_id", sa.Integer(), sa.ForeignKey("markets.id"), nullable=True),
        sa.Column("channel", sa.String(length=40), nullable=True),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("draft_summary", sa.Text(), nullable=True),
        sa.Column("draft_content_json", sa.JSON(), nullable=True),
        sa.Column("published_summary", sa.Text(), nullable=True),
        sa.Column("published_content_json", sa.JSON(), nullable=True),
        sa.Column("published_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("published_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_persona_profiles_profile_key", "persona_profiles", ["profile_key"], unique=True)
    op.create_index("ix_persona_profiles_market_id", "persona_profiles", ["market_id"])
    op.create_index("ix_persona_profiles_channel", "persona_profiles", ["channel"])
    op.create_index("ix_persona_profiles_language", "persona_profiles", ["language"])
    op.create_index("ix_persona_profiles_is_active", "persona_profiles", ["is_active"])
    op.create_index("ix_persona_profiles_published_at", "persona_profiles", ["published_at"])

    op.create_table(
        "persona_profile_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("profile_id", sa.Integer(), sa.ForeignKey("persona_profiles.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("published_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("profile_id", "version", name="uq_persona_profile_version"),
    )
    op.create_index("ix_persona_profile_versions_profile_id", "persona_profile_versions", ["profile_id"])
    op.create_index("ix_persona_profile_versions_version", "persona_profile_versions", ["version"])

    op.create_table(
        "knowledge_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("item_key", sa.String(length=120), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="draft"),
        sa.Column("source_type", sa.String(length=20), nullable=False, server_default="text"),
        sa.Column("market_id", sa.Integer(), sa.ForeignKey("markets.id"), nullable=True),
        sa.Column("channel", sa.String(length=40), nullable=True),
        sa.Column("audience_scope", sa.String(length=40), nullable=False, server_default="customer"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_url", sa.String(length=500), nullable=True),
        sa.Column("file_name", sa.String(length=255), nullable=True),
        sa.Column("file_storage_key", sa.String(length=255), nullable=True),
        sa.Column("mime_type", sa.String(length=120), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("draft_body", sa.Text(), nullable=True),
        sa.Column("draft_normalized_text", sa.Text(), nullable=True),
        sa.Column("published_body", sa.Text(), nullable=True),
        sa.Column("published_normalized_text", sa.Text(), nullable=True),
        sa.Column("published_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("published_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_knowledge_items_item_key", "knowledge_items", ["item_key"], unique=True)
    op.create_index("ix_knowledge_items_status", "knowledge_items", ["status"])
    op.create_index("ix_knowledge_items_source_type", "knowledge_items", ["source_type"])
    op.create_index("ix_knowledge_items_market_id", "knowledge_items", ["market_id"])
    op.create_index("ix_knowledge_items_channel", "knowledge_items", ["channel"])
    op.create_index("ix_knowledge_items_audience_scope", "knowledge_items", ["audience_scope"])
    op.create_index("ix_knowledge_items_priority", "knowledge_items", ["priority"])
    op.create_index("ix_knowledge_items_starts_at", "knowledge_items", ["starts_at"])
    op.create_index("ix_knowledge_items_ends_at", "knowledge_items", ["ends_at"])
    op.create_index("ix_knowledge_items_file_storage_key", "knowledge_items", ["file_storage_key"])
    op.create_index("ix_knowledge_items_published_at", "knowledge_items", ["published_at"])

    op.create_table(
        "knowledge_item_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("knowledge_items.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("published_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("item_id", "version", name="uq_knowledge_item_version"),
    )
    op.create_index("ix_knowledge_item_versions_item_id", "knowledge_item_versions", ["item_id"])
    op.create_index("ix_knowledge_item_versions_version", "knowledge_item_versions", ["version"])

    op.create_table(
        "channel_onboarding_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="pending"),
        sa.Column("requested_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("market_id", sa.Integer(), sa.ForeignKey("markets.id"), nullable=True),
        sa.Column("target_slot", sa.String(length=120), nullable=True),
        sa.Column("desired_display_name", sa.String(length=160), nullable=True),
        sa.Column("desired_channel_account_binding", sa.String(length=160), nullable=True),
        sa.Column("openclaw_account_id", sa.String(length=160), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_channel_onboarding_tasks_provider", "channel_onboarding_tasks", ["provider"])
    op.create_index("ix_channel_onboarding_tasks_status", "channel_onboarding_tasks", ["status"])
    op.create_index("ix_channel_onboarding_tasks_requested_by", "channel_onboarding_tasks", ["requested_by"])
    op.create_index("ix_channel_onboarding_tasks_market_id", "channel_onboarding_tasks", ["market_id"])
    op.create_index("ix_channel_onboarding_tasks_target_slot", "channel_onboarding_tasks", ["target_slot"])
    op.create_index("ix_channel_onboarding_tasks_openclaw_account_id", "channel_onboarding_tasks", ["openclaw_account_id"])
    op.create_index("ix_channel_onboarding_tasks_created_at", "channel_onboarding_tasks", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_channel_onboarding_tasks_created_at", table_name="channel_onboarding_tasks")
    op.drop_index("ix_channel_onboarding_tasks_openclaw_account_id", table_name="channel_onboarding_tasks")
    op.drop_index("ix_channel_onboarding_tasks_target_slot", table_name="channel_onboarding_tasks")
    op.drop_index("ix_channel_onboarding_tasks_market_id", table_name="channel_onboarding_tasks")
    op.drop_index("ix_channel_onboarding_tasks_requested_by", table_name="channel_onboarding_tasks")
    op.drop_index("ix_channel_onboarding_tasks_status", table_name="channel_onboarding_tasks")
    op.drop_index("ix_channel_onboarding_tasks_provider", table_name="channel_onboarding_tasks")
    op.drop_table("channel_onboarding_tasks")

    op.drop_index("ix_knowledge_item_versions_version", table_name="knowledge_item_versions")
    op.drop_index("ix_knowledge_item_versions_item_id", table_name="knowledge_item_versions")
    op.drop_table("knowledge_item_versions")

    op.drop_index("ix_knowledge_items_published_at", table_name="knowledge_items")
    op.drop_index("ix_knowledge_items_file_storage_key", table_name="knowledge_items")
    op.drop_index("ix_knowledge_items_ends_at", table_name="knowledge_items")
    op.drop_index("ix_knowledge_items_starts_at", table_name="knowledge_items")
    op.drop_index("ix_knowledge_items_priority", table_name="knowledge_items")
    op.drop_index("ix_knowledge_items_audience_scope", table_name="knowledge_items")
    op.drop_index("ix_knowledge_items_channel", table_name="knowledge_items")
    op.drop_index("ix_knowledge_items_market_id", table_name="knowledge_items")
    op.drop_index("ix_knowledge_items_source_type", table_name="knowledge_items")
    op.drop_index("ix_knowledge_items_status", table_name="knowledge_items")
    op.drop_index("ix_knowledge_items_item_key", table_name="knowledge_items")
    op.drop_table("knowledge_items")

    op.drop_index("ix_persona_profile_versions_version", table_name="persona_profile_versions")
    op.drop_index("ix_persona_profile_versions_profile_id", table_name="persona_profile_versions")
    op.drop_table("persona_profile_versions")

    op.drop_index("ix_persona_profiles_published_at", table_name="persona_profiles")
    op.drop_index("ix_persona_profiles_is_active", table_name="persona_profiles")
    op.drop_index("ix_persona_profiles_language", table_name="persona_profiles")
    op.drop_index("ix_persona_profiles_channel", table_name="persona_profiles")
    op.drop_index("ix_persona_profiles_market_id", table_name="persona_profiles")
    op.drop_index("ix_persona_profiles_profile_key", table_name="persona_profiles")
    op.drop_table("persona_profiles")
