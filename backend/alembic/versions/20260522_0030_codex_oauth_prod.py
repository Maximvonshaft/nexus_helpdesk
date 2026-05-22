"""codex_oauth_prod

Revision ID: 20260522_0030
Revises: 20260521_0029
Create Date: 2026-05-22 00:00:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20260522_0030'
down_revision = '20260521_0029'
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    if not _has_column('provider_credentials', 'scope'):
        op.add_column('provider_credentials', sa.Column('scope', sa.Text(), nullable=True))
    if not _has_column('provider_auth_sessions', 'scope'):
        op.add_column('provider_auth_sessions', sa.Column('scope', sa.Text(), nullable=True))
    if not _has_column('provider_auth_sessions', 'redirect_uri'):
        op.add_column('provider_auth_sessions', sa.Column('redirect_uri', sa.String(length=512), nullable=True))
    if not _has_column('provider_auth_sessions', 'nonce'):
        op.add_column('provider_auth_sessions', sa.Column('nonce', sa.String(length=255), nullable=True))

    op.create_index('ix_provider_auth_sessions_tenant_provider_status', 'provider_auth_sessions', ['tenant_id', 'provider', 'status'], unique=False)
    op.create_index('ix_provider_auth_sessions_state', 'provider_auth_sessions', ['state'], unique=False)
    op.create_index('ix_provider_credentials_tenant_provider_scope', 'provider_credentials', ['tenant_id', 'provider'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_provider_credentials_tenant_provider_scope', table_name='provider_credentials')
    op.drop_index('ix_provider_auth_sessions_state', table_name='provider_auth_sessions')
    op.drop_index('ix_provider_auth_sessions_tenant_provider_status', table_name='provider_auth_sessions')
    if _has_column('provider_auth_sessions', 'nonce'):
        op.drop_column('provider_auth_sessions', 'nonce')
    if _has_column('provider_auth_sessions', 'redirect_uri'):
        op.drop_column('provider_auth_sessions', 'redirect_uri')
    if _has_column('provider_auth_sessions', 'scope'):
        op.drop_column('provider_auth_sessions', 'scope')
    if _has_column('provider_credentials', 'scope'):
        op.drop_column('provider_credentials', 'scope')
