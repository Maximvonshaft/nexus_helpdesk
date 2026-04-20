"""round5 production hardening

Revision ID: 20260410_0002
Revises: 20260410_0001
Create Date: 2026-04-10 00:10:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = '20260410_0002'
down_revision = '20260410_0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    if 'user_capability_overrides' not in tables:
        op.create_table(
            'user_capability_overrides',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('capability', sa.String(length=120), nullable=False),
            sa.Column('allowed', sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint('user_id', 'capability', name='uq_user_capability_override'),
        )
        op.create_index('ix_user_capability_overrides_user_id', 'user_capability_overrides', ['user_id'])
        op.create_index('ix_user_capability_overrides_capability', 'user_capability_overrides', ['capability'])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    tables = set(inspector.get_table_names())
    if 'user_capability_overrides' in tables:
        op.drop_index('ix_user_capability_overrides_capability', table_name='user_capability_overrides')
        op.drop_index('ix_user_capability_overrides_user_id', table_name='user_capability_overrides')
        op.drop_table('user_capability_overrides')
