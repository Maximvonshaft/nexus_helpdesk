"""Reject the unsupported SLA customer-tier scope.

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

_CONSTRAINT = "ck_sla_revision_customer_tier_unimplemented"


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
            "sla_customer_tier_not_supported: "
            f"{count} revision(s) require explicit remediation"
        )
    with op.batch_alter_table("sla_policy_revisions") as batch:
        batch.create_check_constraint(
            _CONSTRAINT,
            "customer_tier IS NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("sla_policy_revisions") as batch:
        batch.drop_constraint(_CONSTRAINT, type_="check")
