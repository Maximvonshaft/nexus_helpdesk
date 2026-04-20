"""round13 openclaw event and media hardening

Revision ID: 20260410_0009
Revises: 20260410_0008
Create Date: 2026-04-10 00:09:00
"""

from alembic import op
import sqlalchemy as sa

revision = "20260410_0009"
down_revision = "20260410_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "service_heartbeats" not in tables:
        op.create_table(
            "service_heartbeats",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("service_name", sa.String(length=80), nullable=False),
            sa.Column("instance_id", sa.String(length=120), nullable=True),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("details_json", sa.JSON(), nullable=True),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_service_heartbeats_service_name", "service_heartbeats", ["service_name"], unique=True)
        op.create_index("ix_service_heartbeats_last_seen_at", "service_heartbeats", ["last_seen_at"], unique=False)

    existing_indexes = {idx["name"] for idx in inspector.get_indexes("openclaw_attachment_references")} if "openclaw_attachment_references" in tables else set()
    if "ix_openclaw_attachment_refs_storage_status" not in existing_indexes:
        op.create_index("ix_openclaw_attachment_refs_storage_status", "openclaw_attachment_references", ["storage_status"], unique=False)
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("tickets")} if "tickets" in tables else set()
    if "ix_tickets_conversation_state" not in existing_indexes:
        op.create_index("ix_tickets_conversation_state", "tickets", ["conversation_state"], unique=False)


def downgrade() -> None:
    pass
