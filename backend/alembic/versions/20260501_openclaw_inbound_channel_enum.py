"""extend sourcechannel for openclaw inbound discovery

Revision ID: 20260501_openclaw_inbound_channel_enum
Revises: 20260425_round_b_webchat
Create Date: 2026-05-01
"""
from alembic import op
import sqlalchemy as sa

revision = '20260501_openclaw_inbound_channel_enum'
down_revision = '20260425_round_b_webchat'
branch_labels = None
depends_on = None


def _is_postgres() -> bool:
    bind = op.get_bind()
    return bind.dialect.name.startswith('postgresql')


def upgrade() -> None:
    if _is_postgres():
        op.execute("ALTER TYPE sourcechannel ADD VALUE IF NOT EXISTS 'telegram'")
        op.execute("ALTER TYPE sourcechannel ADD VALUE IF NOT EXISTS 'sms'")


def downgrade() -> None:
    # PostgreSQL enum value removal is intentionally omitted because it is not
    # needed for this forward-only production migration path.
    pass
