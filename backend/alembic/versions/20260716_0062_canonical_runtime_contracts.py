"""converge runtime routing on the canonical contracts

Revision ID: 20260716_0062
Revises: 20260715_0061
Create Date: 2026-07-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260716_0062"
down_revision = "20260715_0061"
branch_labels = None
depends_on = None

_CANONICAL_CONTRACT = "nexus.webchat_runtime_reply"
_RETIRED_CONTRACT = "nexus_webchat_runtime_reply_v1"


def _set_contract(source: str, target: str) -> None:
    op.execute(
        sa.text(
            """
            UPDATE provider_routing_rules
            SET output_contract = :target,
                updated_at = CURRENT_TIMESTAMP
            WHERE scenario = 'webchat_runtime_reply'
              AND output_contract = :source
            """
        ).bindparams(source=source, target=target)
    )


def upgrade() -> None:
    _set_contract(_RETIRED_CONTRACT, _CANONICAL_CONTRACT)


def downgrade() -> None:
    _set_contract(_CANONICAL_CONTRACT, _RETIRED_CONTRACT)
