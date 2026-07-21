"""migrate provider routing to the canonical Agent-turn contract

Revision ID: 20260720_0067
Revises: 20260720_0066
Create Date: 2026-07-20

This migration changes only the existing WebChat runtime routing rows and records
exact provenance. It fails closed if an overlapping Agent-turn route already
exists for the same tenant and channel.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260720_0067"
down_revision = "20260720_0066"
branch_labels = None
depends_on = None

_OLD_SCENARIO = "webchat_runtime_reply"
_NEW_SCENARIO = "agent_turn"
_OLD_CONTRACT = "nexus.webchat_runtime_reply"
_NEW_CONTRACT = "nexus.agent_turn.v1"
_PROVENANCE_TABLE = "migration_0067_agent_turn_routes"
_POLICY_PROVENANCE_TABLE = "migration_0067_agent_tool_policies"


def _table_exists(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    if _table_exists(_PROVENANCE_TABLE):
        raise RuntimeError("migration_0067_provenance_table_already_exists")

    bind = op.get_bind()
    conflict_count = bind.execute(
        sa.text(
            """
            SELECT COUNT(*)
            FROM provider_routing_rules AS old_route
            JOIN provider_routing_rules AS new_route
              ON new_route.tenant_id = old_route.tenant_id
             AND new_route.channel_key = old_route.channel_key
             AND new_route.scenario = :new_scenario
            WHERE old_route.scenario = :old_scenario
            """
        ),
        {"old_scenario": _OLD_SCENARIO, "new_scenario": _NEW_SCENARIO},
    ).scalar_one()
    if int(conflict_count or 0) != 0:
        raise RuntimeError(
            "migration_0067_agent_turn_route_conflict: overlapping canonical route exists"
        )

    op.create_table(
        _PROVENANCE_TABLE,
        sa.Column("rule_id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("previous_scenario", sa.String(length=80), nullable=False),
        sa.Column("previous_output_contract", sa.String(length=160), nullable=False),
    )
    op.execute(
        sa.text(
            f"""
            INSERT INTO {_PROVENANCE_TABLE}
                (rule_id, previous_scenario, previous_output_contract)
            SELECT id, scenario, output_contract
            FROM provider_routing_rules
            WHERE scenario = :old_scenario
            """
        ).bindparams(old_scenario=_OLD_SCENARIO)
    )
    op.create_table(
        _POLICY_PROVENANCE_TABLE,
        sa.Column("tool_name", sa.String(length=160), primary_key=True),
        sa.Column("country_code", sa.String(length=16), primary_key=True),
        sa.Column("channel", sa.String(length=40), primary_key=True),
    )
    for tool_name, risk_level, needs_tracking, needs_contact in (
        ("knowledge.search", "low", False, False),
        ("speedaf.order.query", "medium", True, False),
        ("speedaf.express.track.query", "medium", True, False),
        ("speedaf.order.waybillCode.query", "medium", False, True),
    ):
        exists = bind.execute(
            sa.text(
                """
                SELECT COUNT(*) FROM tool_execution_policies
                WHERE tool_name = :tool_name
                  AND country_code = 'GLOBAL'
                  AND channel = 'all'
                """
            ),
            {"tool_name": tool_name},
        ).scalar_one()
        if int(exists or 0) != 0:
            continue
        bind.execute(
            sa.text(
                f"""
                INSERT INTO {_POLICY_PROVENANCE_TABLE}
                    (tool_name, country_code, channel)
                VALUES (:tool_name, 'GLOBAL', 'all')
                """
            ),
            {"tool_name": tool_name},
        )
        bind.execute(
            sa.text(
                """
                INSERT INTO tool_execution_policies
                    (tool_name, country_code, channel, enabled,
                     ai_auto_executable, risk_level,
                     requires_tracking_number, requires_contact,
                     requires_customer_confirmation,
                     requires_human_confirmation, audit_level,
                     created_at, updated_at)
                VALUES
                    (:tool_name, 'GLOBAL', 'all', true,
                     true, :risk_level,
                     :requires_tracking, :requires_contact,
                     false, false, 'standard',
                     CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """
            ),
            {
                "tool_name": tool_name,
                "risk_level": risk_level,
                "requires_tracking": needs_tracking,
                "requires_contact": needs_contact,
            },
        )

    op.execute(
        sa.text(
            f"""
            UPDATE provider_routing_rules
            SET scenario = :new_scenario,
                output_contract = :new_contract,
                updated_at = CURRENT_TIMESTAMP
            WHERE id IN (SELECT rule_id FROM {_PROVENANCE_TABLE})
              AND scenario = :old_scenario
            """
        ).bindparams(
            old_scenario=_OLD_SCENARIO,
            new_scenario=_NEW_SCENARIO,
            new_contract=_NEW_CONTRACT,
        )
    )


def downgrade() -> None:
    if not _table_exists(_PROVENANCE_TABLE):
        raise RuntimeError(
            "migration_0067_downgrade_provenance_missing: refusing to rewrite Agent routes"
        )

    bind = op.get_bind()
    changed_count = bind.execute(
        sa.text(
            f"""
            SELECT COUNT(*)
            FROM provider_routing_rules AS routes
            JOIN {_PROVENANCE_TABLE} AS provenance
              ON provenance.rule_id = routes.id
            WHERE routes.scenario <> :new_scenario
               OR routes.output_contract <> :new_contract
            """
        ),
        {"new_scenario": _NEW_SCENARIO, "new_contract": _NEW_CONTRACT},
    ).scalar_one()
    if int(changed_count or 0) != 0:
        raise RuntimeError(
            "migration_0067_downgrade_conflict: migrated routes changed after upgrade"
        )

    op.execute(
        sa.text(
            f"""
            UPDATE provider_routing_rules
            SET scenario = (
                    SELECT previous_scenario
                    FROM {_PROVENANCE_TABLE}
                    WHERE rule_id = provider_routing_rules.id
                ),
                output_contract = (
                    SELECT previous_output_contract
                    FROM {_PROVENANCE_TABLE}
                    WHERE rule_id = provider_routing_rules.id
                ),
                updated_at = CURRENT_TIMESTAMP
            WHERE id IN (SELECT rule_id FROM {_PROVENANCE_TABLE})
              AND scenario = :new_scenario
              AND output_contract = :new_contract
            """
        ).bindparams(new_scenario=_NEW_SCENARIO, new_contract=_NEW_CONTRACT)
    )
    bind.execute(
        sa.text(
            f"""
            DELETE FROM tool_execution_policies
            WHERE (tool_name, country_code, channel) IN (
                SELECT tool_name, country_code, channel
                FROM {_POLICY_PROVENANCE_TABLE}
            )
            """
        )
    )
    op.drop_table(_POLICY_PROVENANCE_TABLE)
    op.drop_table(_PROVENANCE_TABLE)
