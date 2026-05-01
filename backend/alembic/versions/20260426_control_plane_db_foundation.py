"""control plane database foundation

Revision ID: 20260426_ctrl_foundation
Revises: 20260425_round_b_webchat
Create Date: 2026-04-26
"""

from alembic import op
import sqlalchemy as sa


revision = "20260426_ctrl_foundation"
down_revision = "20260425_round_b_webchat"
branch_labels = None
depends_on = None


def _drop_indexes(table_name: str, names: list[str]) -> None:
    for name in names:
        op.drop_index(name, table_name=table_name)


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
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
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
    for name, columns, unique in [
        ("ix_persona_profiles_profile_key", ["profile_key"], True),
        ("ix_persona_profiles_name", ["name"], False),
        ("ix_persona_profiles_market_id", ["market_id"], False),
        ("ix_persona_profiles_channel", ["channel"], False),
        ("ix_persona_profiles_language", ["language"], False),
        ("ix_persona_profiles_is_active", ["is_active"], False),
        ("ix_persona_profiles_published_at", ["published_at"], False),
        ("ix_persona_profiles_created_by", ["created_by"], False),
        ("ix_persona_profiles_updated_by", ["updated_by"], False),
        ("ix_persona_profiles_published_by", ["published_by"], False),
        ("ix_persona_profiles_created_at", ["created_at"], False),
        ("ix_persona_profiles_updated_at", ["updated_at"], False),
    ]:
        op.create_index(name, "persona_profiles", columns, unique=unique)

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
    for name, columns in [
        ("ix_persona_profile_versions_profile_id", ["profile_id"]),
        ("ix_persona_profile_versions_version", ["version"]),
        ("ix_persona_profile_versions_published_by", ["published_by"]),
        ("ix_persona_profile_versions_published_at", ["published_at"]),
    ]:
        op.create_index(name, "persona_profile_versions", columns)

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
    for name, columns, unique in [
        ("ix_knowledge_items_item_key", ["item_key"], True),
        ("ix_knowledge_items_title", ["title"], False),
        ("ix_knowledge_items_status", ["status"], False),
        ("ix_knowledge_items_source_type", ["source_type"], False),
        ("ix_knowledge_items_market_id", ["market_id"], False),
        ("ix_knowledge_items_channel", ["channel"], False),
        ("ix_knowledge_items_audience_scope", ["audience_scope"], False),
        ("ix_knowledge_items_priority", ["priority"], False),
        ("ix_knowledge_items_starts_at", ["starts_at"], False),
        ("ix_knowledge_items_ends_at", ["ends_at"], False),
        ("ix_knowledge_items_file_storage_key", ["file_storage_key"], False),
        ("ix_knowledge_items_published_at", ["published_at"], False),
        ("ix_knowledge_items_created_by", ["created_by"], False),
        ("ix_knowledge_items_updated_by", ["updated_by"], False),
        ("ix_knowledge_items_published_by", ["published_by"], False),
        ("ix_knowledge_items_created_at", ["created_at"], False),
        ("ix_knowledge_items_updated_at", ["updated_at"], False),
    ]:
        op.create_index(name, "knowledge_items", columns, unique=unique)

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
    for name, columns in [
        ("ix_knowledge_item_versions_item_id", ["item_id"]),
        ("ix_knowledge_item_versions_version", ["version"]),
        ("ix_knowledge_item_versions_published_by", ["published_by"]),
        ("ix_knowledge_item_versions_published_at", ["published_at"]),
    ]:
        op.create_index(name, "knowledge_item_versions", columns)

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
    for name, columns in [
        ("ix_channel_onboarding_tasks_provider", ["provider"]),
        ("ix_channel_onboarding_tasks_status", ["status"]),
        ("ix_channel_onboarding_tasks_requested_by", ["requested_by"]),
        ("ix_channel_onboarding_tasks_market_id", ["market_id"]),
        ("ix_channel_onboarding_tasks_target_slot", ["target_slot"]),
        ("ix_channel_onboarding_tasks_openclaw_account_id", ["openclaw_account_id"]),
        ("ix_channel_onboarding_tasks_created_at", ["created_at"]),
    ]:
        op.create_index(name, "channel_onboarding_tasks", columns)


def downgrade() -> None:
    _drop_indexes(
        "channel_onboarding_tasks",
        [
            "ix_channel_onboarding_tasks_created_at",
            "ix_channel_onboarding_tasks_openclaw_account_id",
            "ix_channel_onboarding_tasks_target_slot",
            "ix_channel_onboarding_tasks_market_id",
            "ix_channel_onboarding_tasks_requested_by",
            "ix_channel_onboarding_tasks_status",
            "ix_channel_onboarding_tasks_provider",
        ],
    )
    op.drop_table("channel_onboarding_tasks")

    _drop_indexes(
        "knowledge_item_versions",
        [
            "ix_knowledge_item_versions_published_at",
            "ix_knowledge_item_versions_published_by",
            "ix_knowledge_item_versions_version",
            "ix_knowledge_item_versions_item_id",
        ],
    )
    op.drop_table("knowledge_item_versions")

    _drop_indexes(
        "knowledge_items",
        [
            "ix_knowledge_items_updated_at",
            "ix_knowledge_items_created_at",
            "ix_knowledge_items_published_by",
            "ix_knowledge_items_updated_by",
            "ix_knowledge_items_created_by",
            "ix_knowledge_items_published_at",
            "ix_knowledge_items_file_storage_key",
            "ix_knowledge_items_ends_at",
            "ix_knowledge_items_starts_at",
            "ix_knowledge_items_priority",
            "ix_knowledge_items_audience_scope",
            "ix_knowledge_items_channel",
            "ix_knowledge_items_market_id",
            "ix_knowledge_items_source_type",
            "ix_knowledge_items_status",
            "ix_knowledge_items_title",
            "ix_knowledge_items_item_key",
        ],
    )
    op.drop_table("knowledge_items")

    _drop_indexes(
        "persona_profile_versions",
        [
            "ix_persona_profile_versions_published_at",
            "ix_persona_profile_versions_published_by",
            "ix_persona_profile_versions_version",
            "ix_persona_profile_versions_profile_id",
        ],
    )
    op.drop_table("persona_profile_versions")

    _drop_indexes(
        "persona_profiles",
        [
            "ix_persona_profiles_updated_at",
            "ix_persona_profiles_created_at",
            "ix_persona_profiles_published_by",
            "ix_persona_profiles_updated_by",
            "ix_persona_profiles_created_by",
            "ix_persona_profiles_published_at",
            "ix_persona_profiles_is_active",
            "ix_persona_profiles_language",
            "ix_persona_profiles_channel",
            "ix_persona_profiles_market_id",
            "ix_persona_profiles_name",
            "ix_persona_profiles_profile_key",
        ],
    )
    op.drop_table("persona_profiles")
