"""multi tenant foundation for tenant-scoped ai profile and knowledge

Revision ID: 20260419_0012
Revises: 20260410_0011
Create Date: 2026-04-19 09:00:00
"""

from __future__ import annotations

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = '20260419_0012'
down_revision = '20260410_0011'
branch_labels = None
depends_on = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def upgrade() -> None:
    op.create_table(
        'tenants',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('slug', sa.String(length=120), nullable=False),
        sa.Column('name', sa.String(length=160), nullable=False),
        sa.Column('status', sa.String(length=40), nullable=False, server_default='active'),
        sa.Column('external_ref', sa.String(length=120), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('slug', name='uq_tenants_slug'),
        sa.UniqueConstraint('name', name='uq_tenants_name'),
        sa.UniqueConstraint('external_ref', name='uq_tenants_external_ref'),
    )
    op.create_index('ix_tenants_slug', 'tenants', ['slug'], unique=True)
    op.create_index('ix_tenants_name', 'tenants', ['name'], unique=True)
    op.create_index('ix_tenants_status', 'tenants', ['status'], unique=False)

    op.create_table(
        'tenant_memberships',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('membership_role', sa.String(length=40), nullable=False, server_default='member'),
        sa.Column('is_default', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('tenant_id', 'user_id', name='uq_tenant_membership'),
    )
    op.create_index('ix_tenant_memberships_tenant_id', 'tenant_memberships', ['tenant_id'], unique=False)
    op.create_index('ix_tenant_memberships_user_id', 'tenant_memberships', ['user_id'], unique=False)
    op.create_index('ix_tenant_memberships_role', 'tenant_memberships', ['membership_role'], unique=False)

    op.create_table(
        'tenant_ai_profiles',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('display_name', sa.String(length=160), nullable=False),
        sa.Column('brand_name', sa.String(length=160), nullable=True),
        sa.Column('role_prompt', sa.Text(), nullable=True),
        sa.Column('tone_style', sa.String(length=120), nullable=True),
        sa.Column('forbidden_claims', sa.JSON(), nullable=True),
        sa.Column('escalation_policy', sa.Text(), nullable=True),
        sa.Column('signature_style', sa.String(length=120), nullable=True),
        sa.Column('language_policy', sa.String(length=160), nullable=True),
        sa.Column('system_prompt_overrides', sa.Text(), nullable=True),
        sa.Column('system_context', sa.JSON(), nullable=True),
        sa.Column('enable_auto_reply', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('enable_auto_summary', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('enable_auto_classification', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('allowed_actions', sa.JSON(), nullable=True),
        sa.Column('default_model_key', sa.String(length=160), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('tenant_id', name='uq_tenant_ai_profile'),
    )
    op.create_index('ix_tenant_ai_profiles_tenant_id', 'tenant_ai_profiles', ['tenant_id'], unique=True)

    op.create_table(
        'tenant_knowledge_entries',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('category', sa.String(length=80), nullable=False, server_default='faq'),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('source_type', sa.String(length=60), nullable=False, server_default='manual'),
        sa.Column('source_ref', sa.String(length=255), nullable=True),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='100'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('tags_json', sa.JSON(), nullable=True),
        sa.Column('metadata_json', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_tenant_knowledge_entries_tenant_id', 'tenant_knowledge_entries', ['tenant_id'], unique=False)
    op.create_index('ix_tenant_knowledge_entries_category', 'tenant_knowledge_entries', ['category'], unique=False)
    op.create_index('ix_tenant_knowledge_entries_priority', 'tenant_knowledge_entries', ['priority'], unique=False)
    op.create_index('ix_tenant_knowledge_entries_is_active', 'tenant_knowledge_entries', ['is_active'], unique=False)

    op.create_table(
        'ticket_tenant_links',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('ticket_id', sa.Integer(), sa.ForeignKey('tickets.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('ticket_id', name='uq_ticket_tenant_ticket'),
        sa.UniqueConstraint('tenant_id', 'ticket_id', name='uq_ticket_tenant_pair'),
    )
    op.create_index('ix_ticket_tenant_links_tenant_id', 'ticket_tenant_links', ['tenant_id'], unique=False)
    op.create_index('ix_ticket_tenant_links_ticket_id', 'ticket_tenant_links', ['ticket_id'], unique=False)

    op.create_table(
        'customer_tenant_links',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('customer_id', sa.Integer(), sa.ForeignKey('customers.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('tenant_id', 'customer_id', name='uq_customer_tenant_pair'),
    )
    op.create_index('ix_customer_tenant_links_tenant_id', 'customer_tenant_links', ['tenant_id'], unique=False)
    op.create_index('ix_customer_tenant_links_customer_id', 'customer_tenant_links', ['customer_id'], unique=False)

    op.create_table(
        'team_tenant_links',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('team_id', sa.Integer(), sa.ForeignKey('teams.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('team_id', name='uq_team_tenant_team'),
        sa.UniqueConstraint('tenant_id', 'team_id', name='uq_team_tenant_pair'),
    )
    op.create_index('ix_team_tenant_links_tenant_id', 'team_tenant_links', ['tenant_id'], unique=False)
    op.create_index('ix_team_tenant_links_team_id', 'team_tenant_links', ['team_id'], unique=False)

    op.create_table(
        'channel_account_tenant_links',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('channel_account_id', sa.Integer(), sa.ForeignKey('channel_accounts.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('channel_account_id', name='uq_channel_account_tenant_account'),
        sa.UniqueConstraint('tenant_id', 'channel_account_id', name='uq_channel_account_tenant_pair'),
    )
    op.create_index('ix_channel_account_tenant_links_tenant_id', 'channel_account_tenant_links', ['tenant_id'], unique=False)
    op.create_index('ix_channel_account_tenant_links_channel_account_id', 'channel_account_tenant_links', ['channel_account_id'], unique=False)

    op.create_table(
        'market_bulletin_tenant_links',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('bulletin_id', sa.Integer(), sa.ForeignKey('market_bulletins.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('bulletin_id', name='uq_market_bulletin_tenant_bulletin'),
        sa.UniqueConstraint('tenant_id', 'bulletin_id', name='uq_market_bulletin_tenant_pair'),
    )
    op.create_index('ix_market_bulletin_tenant_links_tenant_id', 'market_bulletin_tenant_links', ['tenant_id'], unique=False)
    op.create_index('ix_market_bulletin_tenant_links_bulletin_id', 'market_bulletin_tenant_links', ['bulletin_id'], unique=False)

    op.create_table(
        'ai_config_resource_tenant_links',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('resource_id', sa.Integer(), sa.ForeignKey('ai_config_resources.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('resource_id', name='uq_ai_config_resource_tenant_resource'),
        sa.UniqueConstraint('tenant_id', 'resource_id', name='uq_ai_config_resource_tenant_pair'),
    )
    op.create_index('ix_ai_config_resource_tenant_links_tenant_id', 'ai_config_resource_tenant_links', ['tenant_id'], unique=False)
    op.create_index('ix_ai_config_resource_tenant_links_resource_id', 'ai_config_resource_tenant_links', ['resource_id'], unique=False)

    op.create_table(
        'openclaw_conversation_tenant_links',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('tenant_id', sa.Integer(), sa.ForeignKey('tenants.id'), nullable=False),
        sa.Column('conversation_id', sa.Integer(), sa.ForeignKey('openclaw_conversation_links.id'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('conversation_id', name='uq_openclaw_conversation_tenant_conversation'),
        sa.UniqueConstraint('tenant_id', 'conversation_id', name='uq_openclaw_conversation_tenant_pair'),
    )
    op.create_index('ix_openclaw_conversation_tenant_links_tenant_id', 'openclaw_conversation_tenant_links', ['tenant_id'], unique=False)
    op.create_index('ix_openclaw_conversation_tenant_links_conversation_id', 'openclaw_conversation_tenant_links', ['conversation_id'], unique=False)

    bind = op.get_bind()
    now = _utc_now()
    default_slug = 'default'

    existing_tenant_id = bind.execute(sa.text("SELECT id FROM tenants WHERE slug = :slug"), {'slug': default_slug}).scalar()
    if existing_tenant_id is None:
        bind.execute(
            sa.text(
                "INSERT INTO tenants (slug, name, status, external_ref, created_at, updated_at) VALUES (:slug, :name, :status, :external_ref, :created_at, :updated_at)"
            ),
            {
                'slug': default_slug,
                'name': 'Default Tenant',
                'status': 'active',
                'external_ref': 'bootstrap-default-tenant',
                'created_at': now,
                'updated_at': now,
            },
        )
    tenant_id = bind.execute(sa.text("SELECT id FROM tenants WHERE slug = :slug"), {'slug': default_slug}).scalar_one()

    bind.execute(
        sa.text(
            """
            INSERT INTO tenant_memberships (tenant_id, user_id, membership_role, is_default, is_active, created_at, updated_at)
            SELECT :tenant_id, users.id,
                   CASE WHEN users.role IN ('admin', 'manager') THEN 'owner' ELSE 'member' END,
                   1, 1, :now, :now
            FROM users
            WHERE NOT EXISTS (
                SELECT 1 FROM tenant_memberships tm WHERE tm.tenant_id = :tenant_id AND tm.user_id = users.id
            )
            """
        ),
        {'tenant_id': tenant_id, 'now': now},
    )

    bind.execute(
        sa.text(
            """
            INSERT INTO tenant_ai_profiles (tenant_id, display_name, brand_name, role_prompt, tone_style, forbidden_claims, escalation_policy, signature_style, language_policy, system_prompt_overrides, system_context, enable_auto_reply, enable_auto_summary, enable_auto_classification, allowed_actions, default_model_key, created_at, updated_at)
            SELECT :tenant_id, 'Support Assistant', 'Default Tenant',
                   'Act as a helpful and professional customer support representative for the current tenant.',
                   'professional',
                   :forbidden_claims,
                   'Escalate billing, legal, or compensation commitments to a human supervisor.',
                   'Best regards',
                   'Reply in the customer language when confidently detected, otherwise use English.',
                   NULL,
                   :system_context,
                   1, 1, 1,
                   :allowed_actions,
                   NULL,
                   :now,
                   :now
            WHERE NOT EXISTS (SELECT 1 FROM tenant_ai_profiles p WHERE p.tenant_id = :tenant_id)
            """
        ),
        {
            'tenant_id': tenant_id,
            'forbidden_claims': '["Do not invent tracking updates", "Do not promise refunds or compensation without approval"]',
            'system_context': '{"product_scope": "customer support"}',
            'allowed_actions': '["draft_reply", "summarize", "classify"]',
            'now': now,
        },
    )

    for table_name, id_column, link_table, link_column in [
        ('tickets', 'id', 'ticket_tenant_links', 'ticket_id'),
        ('customers', 'id', 'customer_tenant_links', 'customer_id'),
        ('teams', 'id', 'team_tenant_links', 'team_id'),
        ('channel_accounts', 'id', 'channel_account_tenant_links', 'channel_account_id'),
        ('market_bulletins', 'id', 'market_bulletin_tenant_links', 'bulletin_id'),
        ('ai_config_resources', 'id', 'ai_config_resource_tenant_links', 'resource_id'),
        ('openclaw_conversation_links', 'id', 'openclaw_conversation_tenant_links', 'conversation_id'),
    ]:
        bind.execute(
            sa.text(
                f"""
                INSERT INTO {link_table} (tenant_id, {link_column}, created_at)
                SELECT :tenant_id, src.{id_column}, :now
                FROM {table_name} src
                WHERE NOT EXISTS (
                    SELECT 1 FROM {link_table} dst WHERE dst.{link_column} = src.{id_column}
                )
                """
            ),
            {'tenant_id': tenant_id, 'now': now},
        )


def downgrade() -> None:
    for table_name in [
        'openclaw_conversation_tenant_links',
        'ai_config_resource_tenant_links',
        'market_bulletin_tenant_links',
        'channel_account_tenant_links',
        'team_tenant_links',
        'customer_tenant_links',
        'ticket_tenant_links',
        'tenant_knowledge_entries',
        'tenant_ai_profiles',
        'tenant_memberships',
        'tenants',
    ]:
        op.drop_table(table_name)
