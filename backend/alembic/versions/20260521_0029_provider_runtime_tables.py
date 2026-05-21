"""provider_runtime_tables

Revision ID: 20260521_0029
Revises: 20260521_0028
Create Date: 2026-05-21 00:00:00.000000

"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20260521_0029'
down_revision = '20260521_0028'
branch_labels = None
depends_on = None


def _dialect_name() -> str:
    return op.get_bind().dialect.name


def _now_default():
    return sa.text('CURRENT_TIMESTAMP') if _dialect_name() == 'sqlite' else sa.text('now()')


def _active_credential_index_kwargs() -> dict:
    where_clause = sa.text("revoked_at IS NULL")
    dialect_name = _dialect_name()
    if dialect_name == "postgresql":
        return {"postgresql_where": where_clause}
    if dialect_name == "sqlite":
        return {"sqlite_where": where_clause}
    return {}


def upgrade() -> None:
    now_default = _now_default()
    op.create_table(
        'provider_credentials',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('provider', sa.String(length=100), nullable=False),
        sa.Column('provider_runtime', sa.String(length=100), nullable=False),
        sa.Column('credential_type', sa.String(length=50), nullable=False),
        sa.Column('profile_id', sa.String(length=255), nullable=False),
        sa.Column('account_id', sa.String(length=255), nullable=True),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('display_name', sa.String(length=255), nullable=True),
        sa.Column('chatgpt_plan_type', sa.String(length=100), nullable=True),
        sa.Column('encrypted_access_token', sa.Text(), nullable=True),
        sa.Column('encrypted_refresh_token', sa.Text(), nullable=True),
        sa.Column('encrypted_api_key', sa.Text(), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_refresh_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error_code', sa.String(length=255), nullable=True),
        sa.Column('token_fingerprint', sa.String(length=255), nullable=True),
        sa.Column('created_by', sa.String(length=36), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=now_default, nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=now_default, nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_provider_credentials_tenant_provider_profile_active',
        'provider_credentials',
        ['tenant_id', 'provider', 'profile_id'],
        unique=True,
        **_active_credential_index_kwargs(),
    )
    op.create_index('ix_provider_credentials_tenant_provider_status', 'provider_credentials', ['tenant_id', 'provider', 'status'])
    op.create_index('ix_provider_credentials_expires_at', 'provider_credentials', ['expires_at'])
    op.create_index('ix_provider_credentials_token_fingerprint', 'provider_credentials', ['token_fingerprint'])

    op.create_table(
        'provider_auth_sessions',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('provider', sa.String(length=100), nullable=False),
        sa.Column('flow_type', sa.String(length=50), nullable=False),
        sa.Column('state', sa.String(length=255), nullable=False),
        sa.Column('code_verifier', sa.String(length=255), nullable=True),
        sa.Column('device_auth_id', sa.String(length=255), nullable=True),
        sa.Column('user_code', sa.String(length=100), nullable=True),
        sa.Column('verification_url', sa.String(length=512), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=False),
        sa.Column('error_code', sa.String(length=255), nullable=True),
        sa.Column('created_by', sa.String(length=36), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=now_default, nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'provider_runtime_audit_logs',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('provider', sa.String(length=100), nullable=False),
        sa.Column('credential_id', sa.String(length=36), nullable=True),
        sa.Column('request_id', sa.String(length=100), nullable=False),
        sa.Column('channel_key', sa.String(length=100), nullable=False),
        sa.Column('session_id', sa.String(length=100), nullable=True),
        sa.Column('operation', sa.String(length=50), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=False),
        sa.Column('safe_summary', sa.JSON(), nullable=True),
        sa.Column('error_code', sa.String(length=255), nullable=True),
        sa.Column('elapsed_ms', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=now_default, nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'provider_routing_rules',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('tenant_id', sa.String(length=36), nullable=False),
        sa.Column('channel_key', sa.String(length=100), nullable=False),
        sa.Column('scenario', sa.String(length=100), nullable=False),
        sa.Column('primary_provider', sa.String(length=100), nullable=False),
        sa.Column('fallback_providers', sa.JSON(), nullable=True),
        sa.Column('output_contract', sa.String(length=100), nullable=False),
        sa.Column('timeout_ms', sa.Integer(), nullable=False),
        sa.Column('canary_percent', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('kill_switch', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=now_default, nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=now_default, nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'channel_key', 'scenario', name='uq_provider_routing_rules_tenant_channel_scenario'),
    )


def downgrade() -> None:
    op.drop_table('provider_routing_rules')
    op.drop_table('provider_runtime_audit_logs')
    op.drop_table('provider_auth_sessions')
    op.drop_index('ix_provider_credentials_token_fingerprint', table_name='provider_credentials')
    op.drop_index('ix_provider_credentials_expires_at', table_name='provider_credentials')
    op.drop_index('ix_provider_credentials_tenant_provider_status', table_name='provider_credentials')
    op.drop_index('ix_provider_credentials_tenant_provider_profile_active', table_name='provider_credentials')
    op.drop_table('provider_credentials')
