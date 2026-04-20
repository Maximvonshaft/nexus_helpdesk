"""round10 signoff indexes

Revision ID: 20260410_0007
Revises: 20260410_0006
Create Date: 2026-04-10 00:07:00
"""

from alembic import op
import sqlalchemy as sa

revision = "20260410_0007"
down_revision = "20260410_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "tickets" in tables:
        existing = {idx["name"] for idx in inspector.get_indexes("tickets")}
        if "ix_tickets_market_country_status" not in existing:
            op.create_index("ix_tickets_market_country_status", "tickets", ["market_id", "country_code", "status"], unique=False)
    if "openclaw_conversation_links" in tables:
        existing = {idx["name"] for idx in inspector.get_indexes("openclaw_conversation_links")}
        if "ix_openclaw_links_session_updated" not in existing:
            op.create_index("ix_openclaw_links_session_updated", "openclaw_conversation_links", ["session_key", "updated_at"], unique=False)


def downgrade() -> None:
    for name, table in [
        ("ix_openclaw_links_session_updated", "openclaw_conversation_links"),
        ("ix_tickets_market_country_status", "tickets"),
    ]:
        try:
            op.drop_index(name, table_name=table)
        except Exception:
            pass
