"""tenant openclaw projection table

Revision ID: 20260419_0013
Revises: 20260419_0012
Create Date: 2026-04-19 11:00:00
"""

from alembic import op
import sqlalchemy as sa

revision = '20260419_0013'
down_revision = '20260419_0012'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'tenant_openclaw_agents',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('openclaw_agent_id', sa.String(length=160), nullable=False),
        sa.Column('agent_name', sa.String(length=160), nullable=False),
        sa.Column('workspace_dir', sa.String(length=500), nullable=False),
        sa.Column('deployment_mode', sa.String(length=40), nullable=False, server_default='shared_gateway'),
        sa.Column('binding_scope', sa.String(length=120), nullable=False, server_default='tenant_default'),
        sa.Column('binding_summary', sa.JSON(), nullable=True),
        sa.Column('identity_sync_status', sa.String(length=40), nullable=False, server_default='pending'),
        sa.Column('knowledge_sync_status', sa.String(length=40), nullable=False, server_default='pending'),
        sa.Column('identity_preview', sa.Text(), nullable=True),
        sa.Column('bootstrap_preview', sa.Text(), nullable=True),
        sa.Column('last_projected_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_projection_error', sa.Text(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('tenant_id', name='uq_tenant_openclaw_agent'),
    )
    op.create_index('ix_tenant_openclaw_agents_tenant_id', 'tenant_openclaw_agents', ['tenant_id'], unique=True)
    op.create_index('ix_tenant_openclaw_agents_openclaw_agent_id', 'tenant_openclaw_agents', ['openclaw_agent_id'], unique=False)
    op.create_index('ix_tenant_openclaw_agents_is_active', 'tenant_openclaw_agents', ['is_active'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_tenant_openclaw_agents_is_active', table_name='tenant_openclaw_agents')
    op.drop_index('ix_tenant_openclaw_agents_openclaw_agent_id', table_name='tenant_openclaw_agents')
    op.drop_index('ix_tenant_openclaw_agents_tenant_id', table_name='tenant_openclaw_agents')
    op.drop_table('tenant_openclaw_agents')
