"""add expiring Agent Session checkpoints and governed Specialist routing

Revision ID: 20260721_0071
Revises: 20260721_0070
Create Date: 2026-07-21
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "20260721_0071"
down_revision = "20260721_0070"
branch_labels = None
depends_on = None

_ROUTE_PROVENANCE = "migration_0071_agent_specialist_routes"
_SPECIALIST_SCENARIO = "agent_specialist"
_SPECIALIST_CONTRACT = "nexus.agent_specialist.v1"


def upgrade() -> None:
    op.create_table(
        "agent_session_checkpoints",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_key", sa.String(length=80), nullable=False),
        sa.Column("session_id", sa.String(length=160), nullable=False),
        sa.Column(
            "release_id",
            sa.Integer(),
            sa.ForeignKey("agent_releases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_run_id",
            sa.Integer(),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("summary_sha256", sa.String(length=64), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("estimated_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "tenant_key",
            "session_id",
            "version",
            name="uq_agent_session_checkpoint_version",
        ),
        sa.CheckConstraint(
            "version > 0", name="ck_agent_session_checkpoint_version"
        ),
        sa.CheckConstraint(
            "estimated_tokens >= 0",
            name="ck_agent_session_checkpoint_tokens_nonnegative",
        ),
    )
    for column in (
        "tenant_key",
        "session_id",
        "release_id",
        "source_run_id",
        "version",
        "summary_sha256",
        "is_active",
        "created_at",
        "expires_at",
        "deactivated_at",
    ):
        op.create_index(
            f"ix_agent_session_checkpoints_{column}",
            "agent_session_checkpoints",
            [column],
        )
    op.create_index(
        "ix_agent_session_checkpoints_active",
        "agent_session_checkpoints",
        ["tenant_key", "session_id", "is_active", "created_at"],
    )

    bind = op.get_bind()
    conflict = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM provider_routing_rules "
            "WHERE scenario = :scenario"
        ),
        {"scenario": _SPECIALIST_SCENARIO},
    ).scalar_one()
    if int(conflict or 0):
        raise RuntimeError("migration_0071_specialist_route_conflict")

    op.create_table(
        _ROUTE_PROVENANCE,
        sa.Column("rule_id", sa.String(length=36), primary_key=True),
        sa.Column("source_rule_id", sa.String(length=36), nullable=False),
    )
    routes = sa.table(
        "provider_routing_rules",
        sa.column("id", sa.String(length=36)),
        sa.column("tenant_id", sa.String(length=36)),
        sa.column("channel_key", sa.String(length=100)),
        sa.column("scenario", sa.String(length=100)),
        sa.column("primary_provider", sa.String(length=100)),
        sa.column("fallback_providers", sa.JSON()),
        sa.column("output_contract", sa.String(length=100)),
        sa.column("timeout_ms", sa.Integer()),
        sa.column("canary_percent", sa.Integer()),
        sa.column("kill_switch", sa.Boolean()),
        sa.column("enabled", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    provenance = sa.table(
        _ROUTE_PROVENANCE,
        sa.column("rule_id", sa.String(length=36)),
        sa.column("source_rule_id", sa.String(length=36)),
    )
    source_rows = bind.execute(
        sa.select(routes).where(routes.c.scenario == "agent_turn")
    ).mappings().all()
    now = datetime.now(timezone.utc)
    for source in source_rows:
        rule_id = str(uuid.uuid4())
        bind.execute(
            sa.insert(provenance).values(
                rule_id=rule_id,
                source_rule_id=str(source["id"]),
            )
        )
        bind.execute(
            sa.insert(routes).values(
                id=rule_id,
                tenant_id=source["tenant_id"],
                channel_key=source["channel_key"],
                scenario=_SPECIALIST_SCENARIO,
                primary_provider=source["primary_provider"],
                fallback_providers=source["fallback_providers"],
                output_contract=_SPECIALIST_CONTRACT,
                timeout_ms=source["timeout_ms"],
                canary_percent=source["canary_percent"],
                kill_switch=source["kill_switch"],
                enabled=source["enabled"],
                created_at=now,
                updated_at=now,
            )
        )

    existing = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM tool_execution_policies "
            "WHERE tool_name = 'specialist.delegate' "
            "AND country_code = 'GLOBAL' AND channel = 'all'"
        )
    ).scalar_one()
    if int(existing or 0):
        raise RuntimeError("migration_0071_tool_policy_conflict:specialist.delegate")
    bind.execute(
        sa.text(
            """
            INSERT INTO tool_execution_policies
                (tool_name, country_code, channel, enabled, ai_auto_executable,
                 risk_level, requires_tracking_number, requires_contact,
                 requires_customer_confirmation, requires_human_confirmation,
                 audit_level, created_at, updated_at)
            VALUES
                ('specialist.delegate', 'GLOBAL', 'all', true, true, 'medium',
                 false, false, false, false, 'detailed',
                 CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    changed = bind.execute(
        sa.text(
            f"""
            SELECT COUNT(*)
            FROM provider_routing_rules AS routes
            JOIN {_ROUTE_PROVENANCE} AS provenance
              ON provenance.rule_id = routes.id
            WHERE routes.scenario <> :scenario
               OR routes.output_contract <> :contract
            """
        ),
        {"scenario": _SPECIALIST_SCENARIO, "contract": _SPECIALIST_CONTRACT},
    ).scalar_one()
    if int(changed or 0):
        raise RuntimeError("migration_0071_specialist_route_downgrade_conflict")
    bind.execute(
        sa.text(
            f"DELETE FROM provider_routing_rules "
            f"WHERE id IN (SELECT rule_id FROM {_ROUTE_PROVENANCE})"
        )
    )
    op.drop_table(_ROUTE_PROVENANCE)
    bind.execute(
        sa.text(
            "DELETE FROM tool_execution_policies "
            "WHERE tool_name = 'specialist.delegate' "
            "AND country_code = 'GLOBAL' AND channel = 'all'"
        )
    )
    op.drop_table("agent_session_checkpoints")
