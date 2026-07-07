"""cleanup retired WebChat runtime routing

Revision ID: 20260706_0050
Revises: 20260704_0049
Create Date: 2026-07-06
"""

from __future__ import annotations

from alembic import op


revision = "20260706_0050"
down_revision = "20260704_0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DELETE FROM provider_routing_rules
        WHERE tenant_id = 'default'
          AND channel_key = 'website'
          AND scenario = 'webchat_fast_reply'
    """)
    op.execute("""
        INSERT INTO provider_routing_rules (
            id,
            tenant_id,
            channel_key,
            scenario,
            primary_provider,
            fallback_providers,
            output_contract,
            timeout_ms,
            canary_percent,
            kill_switch,
            enabled,
            created_at,
            updated_at
        )
        VALUES (
            '00000000-0050-4000-8000-000000000050',
            'default',
            'website',
            'webchat_runtime_reply',
            'private_ai_runtime',
            '[]',
            'nexus_webchat_runtime_reply_v1',
            10000,
            100,
            false,
            true,
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP
        )
        ON CONFLICT (tenant_id, channel_key, scenario) DO UPDATE SET
            primary_provider = 'private_ai_runtime',
            fallback_providers = '[]',
            output_contract = 'nexus_webchat_runtime_reply_v1',
            timeout_ms = 10000,
            canary_percent = 100,
            kill_switch = false,
            enabled = true,
            updated_at = CURRENT_TIMESTAMP
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM provider_routing_rules
        WHERE tenant_id = 'default'
          AND channel_key = 'website'
          AND scenario = 'webchat_runtime_reply'
          AND id = '00000000-0050-4000-8000-000000000050'
    """)
