"""Permanently remove the unsupported SLA customer-tier scope.

Revision ID: 20260727_aud9
Revises: 20260727_aud8
Create Date: 2026-07-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260727_aud9"
down_revision = "20260727_aud8"
branch_labels = None
depends_on = None

_SCOPE_INDEX = "ix_sla_revision_scope_effective"
_TIER_INDEX = "ix_sla_policy_revisions_customer_tier"


def upgrade() -> None:
    bind = op.get_bind()
    count = int(
        bind.execute(
            sa.text(
                "SELECT count(*) FROM sla_policy_revisions "
                "WHERE customer_tier IS NOT NULL"
            )
        ).scalar()
        or 0
    )
    if count:
        raise RuntimeError(
            "sla_customer_tier_data_requires_explicit_remediation: "
            f"{count} revision(s) contain unsupported values"
        )

    with op.batch_alter_table("sla_policy_revisions") as batch:
        batch.drop_index(_SCOPE_INDEX)
        batch.drop_index(_TIER_INDEX)
        batch.drop_column("customer_tier")
        batch.create_index(
            _SCOPE_INDEX,
            [
                "tenant_id",
                "market_id",
                "channel_key",
                "scenario_key",
                "status",
                "effective_from",
            ],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("sla_policy_revisions") as batch:
        batch.drop_index(_SCOPE_INDEX)
        batch.add_column(
            sa.Column("customer_tier", sa.String(length=80), nullable=True)
        )
        batch.create_index(_TIER_INDEX, ["customer_tier"], unique=False)
        batch.create_index(
            _SCOPE_INDEX,
            [
                "tenant_id",
                "market_id",
                "channel_key",
                "scenario_key",
                "customer_tier",
                "status",
                "effective_from",
            ],
            unique=False,
        )
