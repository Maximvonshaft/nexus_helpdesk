"""extend sourcechannel for openclaw inbound discovery

Revision ID: 20260501_0012
Revises: 20260425_round_b_webchat
Create Date: 2026-05-01
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = '20260501_0012'
down_revision = '20260425_round_b_webchat'
branch_labels = None
depends_on = None


def _is_postgres() -> bool:
    bind = op.get_bind()
    return bind.dialect.name.startswith('postgresql')


def _enum_exists(enum_name: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(text("SELECT 1 FROM pg_type WHERE typname = :name LIMIT 1"), {"name": enum_name}).scalar()
    return bool(result)


def upgrade() -> None:
    if _is_postgres() and _enum_exists('sourcechannel'):
        op.execute("ALTER TYPE sourcechannel ADD VALUE IF NOT EXISTS 'telegram'")
        op.execute("ALTER TYPE sourcechannel ADD VALUE IF NOT EXISTS 'sms'")


def downgrade() -> None:
    # PostgreSQL enum value removal is intentionally omitted because it is not
    # needed for this forward-only production migration path.
    pass
