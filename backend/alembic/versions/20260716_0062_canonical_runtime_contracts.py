"""converge runtime routing on the canonical contracts

Revision ID: 20260716_0062
Revises: 20260715_0061
Create Date: 2026-07-16

The migration records exactly which routing rules it changes. Downgrade restores
only those rows and fails closed when provenance is absent or the rows have been
modified after upgrade.
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
_PROVENANCE_TABLE = "migration_0062_runtime_contract_rows"


def _table_exists() -> bool:
    return sa.inspect(op.get_bind()).has_table(_PROVENANCE_TABLE)


def upgrade() -> None:
    if _table_exists():
        raise RuntimeError("migration_0062_provenance_table_already_exists")

    op.create_table(
        _PROVENANCE_TABLE,
        sa.Column("rule_id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("previous_output_contract", sa.String(length=160), nullable=False),
    )
    op.execute(
        sa.text(
            f"""
            INSERT INTO {_PROVENANCE_TABLE} (rule_id, previous_output_contract)
            SELECT id, output_contract
            FROM provider_routing_rules
            WHERE scenario = 'webchat_runtime_reply'
              AND output_contract = :retired
            """
        ).bindparams(retired=_RETIRED_CONTRACT)
    )
    op.execute(
        sa.text(
            f"""
            UPDATE provider_routing_rules
            SET output_contract = :canonical,
                updated_at = CURRENT_TIMESTAMP
            WHERE id IN (SELECT rule_id FROM {_PROVENANCE_TABLE})
              AND scenario = 'webchat_runtime_reply'
              AND output_contract = :retired
            """
        ).bindparams(
            canonical=_CANONICAL_CONTRACT,
            retired=_RETIRED_CONTRACT,
        )
    )


def downgrade() -> None:
    if not _table_exists():
        raise RuntimeError(
            "migration_0062_downgrade_provenance_missing: refusing to rewrite canonical routing rules"
        )

    bind = op.get_bind()
    changed_after_upgrade = bind.execute(
        sa.text(
            f"""
            SELECT COUNT(*)
            FROM provider_routing_rules AS rules
            JOIN {_PROVENANCE_TABLE} AS provenance
              ON provenance.rule_id = rules.id
            WHERE rules.scenario <> 'webchat_runtime_reply'
               OR rules.output_contract <> :canonical
               OR provenance.previous_output_contract <> :retired
            """
        ).bindparams(
            canonical=_CANONICAL_CONTRACT,
            retired=_RETIRED_CONTRACT,
        )
    ).scalar_one()
    if int(changed_after_upgrade or 0) != 0:
        raise RuntimeError(
            "migration_0062_downgrade_conflict: migrated routing rules changed after upgrade"
        )

    op.execute(
        sa.text(
            f"""
            UPDATE provider_routing_rules
            SET output_contract = (
                    SELECT previous_output_contract
                    FROM {_PROVENANCE_TABLE}
                    WHERE rule_id = provider_routing_rules.id
                ),
                updated_at = CURRENT_TIMESTAMP
            WHERE id IN (SELECT rule_id FROM {_PROVENANCE_TABLE})
              AND scenario = 'webchat_runtime_reply'
              AND output_contract = :canonical
            """
        ).bindparams(canonical=_CANONICAL_CONTRACT)
    )
    op.drop_table(_PROVENANCE_TABLE)
