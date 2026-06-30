"""provider runtime sss production defaults

Revision ID: 20260523_0032
Revises: 20260523_wcall_ai2
Create Date: 2026-05-23
"""
from __future__ import annotations

from alembic import op

revision = "20260523_0032"
down_revision = "20260523_wcall_ai2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE provider_routing_rules
        SET fallback_providers = '["external_channel_responses","rule_engine"]',
            canary_percent = 0,
            kill_switch = false,
            enabled = true,
            updated_at = CURRENT_TIMESTAMP
        WHERE tenant_id = 'default'
          AND channel_key = 'website'
          AND scenario = 'webchat_fast_reply'
          AND primary_provider = 'codex_app_server'
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE provider_routing_rules
        SET fallback_providers = '[]',
            canary_percent = 100,
            kill_switch = false,
            updated_at = CURRENT_TIMESTAMP
        WHERE tenant_id = 'default'
          AND channel_key = 'website'
          AND scenario = 'webchat_fast_reply'
          AND primary_provider = 'codex_app_server'
    """)
