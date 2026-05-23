"""seed_provider_runtime_webchat_fast_reply

Revision ID: 20260522_0031
Revises: 20260522_0030
Create Date: 2026-05-22 00:00:00.000000

"""
from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = '20260522_0031'
down_revision = '20260522_0030'
branch_labels = None
depends_on = None


def upgrade() -> None:
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
            '00000000-0031-4000-8000-000000000031',
            'default',
            'website',
            'webchat_fast_reply',
            'codex_app_server',
            '["openclaw_responses","rule_engine"]',
            'speedaf_webchat_fast_reply_v1',
            10000,
            0,
            false,
            true,
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP
        )
        ON CONFLICT (tenant_id, channel_key, scenario) DO UPDATE SET
            primary_provider = excluded.primary_provider,
            fallback_providers = excluded.fallback_providers,
            output_contract = excluded.output_contract,
            timeout_ms = excluded.timeout_ms,
            canary_percent = excluded.canary_percent,
            kill_switch = excluded.kill_switch,
            enabled = excluded.enabled,
            updated_at = CURRENT_TIMESTAMP
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM provider_routing_rules
        WHERE tenant_id = 'default'
          AND channel_key = 'website'
          AND scenario = 'webchat_fast_reply'
          AND id = '00000000-0031-4000-8000-000000000031'
    """)
