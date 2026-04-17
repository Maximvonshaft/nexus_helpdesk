"""round9 openclaw queue ops

Revision ID: 20260410_0006
Revises: 20260410_0005
Create Date: 2026-04-10 00:06:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260410_0006"
down_revision = "20260410_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("background_jobs")}
    if "dedupe_key" not in columns:
        op.add_column("background_jobs", sa.Column("dedupe_key", sa.String(length=255), nullable=True))
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("background_jobs")}
    if "ix_background_jobs_dedupe_key" not in existing_indexes:
        op.create_index("ix_background_jobs_dedupe_key", "background_jobs", ["dedupe_key"], unique=False)
    if "ix_openclaw_links_ticket_updated" not in {idx["name"] for idx in inspector.get_indexes("openclaw_conversation_links")}:
        op.create_index("ix_openclaw_links_ticket_updated", "openclaw_conversation_links", ["ticket_id", "updated_at"], unique=False)
    if "ix_openclaw_transcript_ticket_received" not in {idx["name"] for idx in inspector.get_indexes("openclaw_transcript_messages")}:
        op.create_index("ix_openclaw_transcript_ticket_received", "openclaw_transcript_messages", ["ticket_id", "received_at"], unique=False)


def downgrade() -> None:
    for name, table in [
        ("ix_openclaw_transcript_ticket_received", "openclaw_transcript_messages"),
        ("ix_openclaw_links_ticket_updated", "openclaw_conversation_links"),
        ("ix_background_jobs_dedupe_key", "background_jobs"),
    ]:
        try:
            op.drop_index(name, table_name=table)
        except Exception:
            pass
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns("background_jobs")}
    if "dedupe_key" in columns:
        op.drop_column("background_jobs", "dedupe_key")
