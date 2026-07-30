"""Add Scenario-governed Handoff routing plan and bounded candidate attempts.

Revision ID: 20260728_r6_routing
Revises: 20260728_r5_scenario
Create Date: 2026-07-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260728_r6_routing"
down_revision = "20260728_r5_scenario"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("operator_queue_scope_grants") as batch:
        batch.add_column(
            sa.Column(
                "queue_key",
                sa.String(length=160),
                nullable=False,
                server_default="legacy",
            )
        )
        batch.drop_constraint(
            "uq_operator_queue_scope_grant",
            type_="unique",
        )
        batch.create_unique_constraint(
            "uq_operator_queue_scope_grant",
            [
                "user_id",
                "tenant_key",
                "country_code",
                "channel_key",
                "queue_key",
            ],
        )
    op.create_index(
        "ix_operator_queue_scope_grants_route",
        "operator_queue_scope_grants",
        ["tenant_key", "country_code", "channel_key", "queue_key", "enabled"],
        unique=False,
    )

    op.create_table(
        "handoff_routing_plans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("ticket_id", sa.Integer(), nullable=False),
        sa.Column("scenario_assignment_id", sa.Integer(), nullable=False),
        sa.Column("scenario_key", sa.String(length=160), nullable=False),
        sa.Column("catalog_sha256", sa.String(length=64), nullable=False),
        sa.Column("scenario_snapshot_sha256", sa.String(length=64), nullable=False),
        sa.Column("owner_queue_key", sa.String(length=160), nullable=False),
        sa.Column("required_capabilities_json", sa.Text(), nullable=False),
        sa.Column("risk_level", sa.String(length=24), nullable=False),
        sa.Column("escalation_policy_key", sa.String(length=160), nullable=True),
        sa.Column("plan_schema", sa.String(length=80), nullable=False),
        sa.Column("plan_digest", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_generation", sa.Integer(), nullable=False),
        sa.Column("max_generations", sa.Integer(), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assigned_agent_id", sa.Integer(), nullable=True),
        sa.Column("outcome_code", sa.String(length=160), nullable=True),
        sa.Column("exhausted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'retry_scheduled', 'assigned', 'exhausted', 'closed')",
            name="ck_handoff_routing_plan_status",
        ),
        sa.CheckConstraint(
            "current_generation BETWEEN 1 AND 10",
            name="ck_handoff_routing_plan_generation",
        ),
        sa.CheckConstraint(
            "max_generations BETWEEN 1 AND 10",
            name="ck_handoff_routing_plan_max_generations",
        ),
        sa.CheckConstraint(
            "current_generation <= max_generations",
            name="ck_handoff_routing_plan_generation_bound",
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["webchat_handoff_requests.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["tickets.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["scenario_assignment_id"],
            ["case_scenario_assignments.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_agent_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("request_id", name="uq_handoff_routing_plan_request"),
    )
    for name, columns in (
        ("ix_handoff_routing_plans_request_id", ["request_id"]),
        ("ix_handoff_routing_plans_ticket_id", ["ticket_id"]),
        (
            "ix_handoff_routing_plans_scenario_assignment_id",
            ["scenario_assignment_id"],
        ),
        ("ix_handoff_routing_plans_scenario_key", ["scenario_key"]),
        ("ix_handoff_routing_plans_owner_queue_key", ["owner_queue_key"]),
        ("ix_handoff_routing_plans_risk_level", ["risk_level"]),
        (
            "ix_handoff_routing_plans_escalation_policy_key",
            ["escalation_policy_key"],
        ),
        ("ix_handoff_routing_plans_plan_digest", ["plan_digest"]),
        ("ix_handoff_routing_plans_status", ["status"]),
        ("ix_handoff_routing_plans_next_retry_at", ["next_retry_at"]),
        ("ix_handoff_routing_plans_assigned_agent_id", ["assigned_agent_id"]),
        ("ix_handoff_routing_plans_outcome_code", ["outcome_code"]),
        ("ix_handoff_routing_plans_exhausted_at", ["exhausted_at"]),
        ("ix_handoff_routing_plans_created_at", ["created_at"]),
        ("ix_handoff_routing_plans_updated_at", ["updated_at"]),
        ("ix_handoff_routing_plan_retry", ["status", "next_retry_at"]),
        (
            "ix_handoff_routing_plan_queue_status",
            ["owner_queue_key", "status"],
        ),
    ):
        op.create_index(name, "handoff_routing_plans", columns, unique=False)

    op.create_table(
        "handoff_routing_candidate_attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("agent_id", sa.Integer(), nullable=False),
        sa.Column("channel_kind", sa.String(length=16), nullable=False),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        sa.Column("reason_code", sa.String(length=160), nullable=True),
        sa.Column("external_ref", sa.String(length=160), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "channel_kind IN ('text', 'voice', 'manual')",
            name="ck_handoff_routing_attempt_channel",
        ),
        sa.CheckConstraint(
            "outcome IN ('offered', 'accepted', 'declined', 'expired', 'cancelled', 'unavailable')",
            name="ck_handoff_routing_attempt_outcome",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["handoff_routing_plans.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["webchat_handoff_requests.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "plan_id",
            "generation",
            "agent_id",
            "channel_kind",
            name="uq_handoff_routing_attempt_candidate_generation",
        ),
    )
    for name, columns in (
        ("ix_handoff_routing_candidate_attempts_plan_id", ["plan_id"]),
        ("ix_handoff_routing_candidate_attempts_request_id", ["request_id"]),
        ("ix_handoff_routing_candidate_attempts_generation", ["generation"]),
        ("ix_handoff_routing_candidate_attempts_agent_id", ["agent_id"]),
        ("ix_handoff_routing_candidate_attempts_channel_kind", ["channel_kind"]),
        ("ix_handoff_routing_candidate_attempts_outcome", ["outcome"]),
        ("ix_handoff_routing_candidate_attempts_external_ref", ["external_ref"]),
        ("ix_handoff_routing_candidate_attempts_created_at", ["created_at"]),
        ("ix_handoff_routing_candidate_attempts_updated_at", ["updated_at"]),
        (
            "ix_handoff_routing_attempt_generation",
            ["plan_id", "generation", "outcome"],
        ),
    ):
        op.create_index(
            name,
            "handoff_routing_candidate_attempts",
            columns,
            unique=False,
        )


def _assert_legacy_grant_identity_representable() -> None:
    """Fail before DDL when the old four-column grant identity cannot represent data."""

    duplicate = op.get_bind().execute(
        sa.text(
            "SELECT user_id, tenant_key, country_code, channel_key "
            "FROM operator_queue_scope_grants "
            "GROUP BY user_id, tenant_key, country_code, channel_key "
            "HAVING count(*) > 1 LIMIT 1"
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            "r6_handoff_routing_downgrade_blocked_multi_queue_grants"
        )


def downgrade() -> None:
    _assert_legacy_grant_identity_representable()
    op.drop_table("handoff_routing_candidate_attempts")
    op.drop_table("handoff_routing_plans")
    op.drop_index(
        "ix_operator_queue_scope_grants_route",
        table_name="operator_queue_scope_grants",
    )
    with op.batch_alter_table("operator_queue_scope_grants") as batch:
        batch.drop_constraint(
            "uq_operator_queue_scope_grant",
            type_="unique",
        )
        batch.create_unique_constraint(
            "uq_operator_queue_scope_grant",
            ["user_id", "tenant_key", "country_code", "channel_key"],
        )
        batch.drop_column("queue_key")
