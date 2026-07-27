"""Add governed routing execution state to the canonical HandoffRequest.

Revision ID: 20260727_r4p1b
Revises: 20260727_r4p1a
Create Date: 2026-07-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260727_r4p1b"
down_revision = "20260727_r4p1a"
branch_labels = None
depends_on = None


_ROUTING_OUTCOMES = (
    "waiting",
    "offered",
    "accepted",
    "all_declined",
    "capacity_exhausted",
    "skill_unavailable",
    "scheduled_retry",
    "escalated",
    "fallback_selected",
)


def _retire_mutable_scenario_sla_projections(bind) -> None:
    """Remove only derivations created before frozen Scenario assignment existed.

    TicketSLAPauseInterval remains append-only. The canonical SLA service lazily
    reselects an approved revision from TicketScenarioAssignment and reconstructs
    TicketSLATarget from the original Case creation time plus pause history.
    """

    bind.execute(
        sa.text(
            "DELETE FROM ticket_sla_targets WHERE ticket_id IN ("
            " SELECT ticket_id FROM ticket_scenario_assignments"
            ")"
        )
    )
    bind.execute(
        sa.text(
            "DELETE FROM ticket_sla_assignments WHERE ticket_id IN ("
            " SELECT ticket_id FROM ticket_scenario_assignments"
            ")"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE tickets SET "
            "sla_policy_id = NULL, "
            "first_response_due_at = NULL, "
            "resolution_due_at = NULL, "
            "sla_paused = false, "
            "sla_paused_at = NULL, "
            "sla_pause_reason = NULL, "
            "first_response_breached = false, "
            "resolution_breached = false "
            "WHERE id IN (SELECT ticket_id FROM ticket_scenario_assignments)"
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    _retire_mutable_scenario_sla_projections(bind)

    op.add_column(
        "webchat_handoff_requests",
        sa.Column(
            "routing_generation",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column(
        "webchat_handoff_requests",
        sa.Column(
            "routing_outcome",
            sa.String(length=40),
            nullable=False,
            server_default="waiting",
        ),
    )
    op.add_column(
        "webchat_handoff_requests",
        sa.Column("routing_reason_code", sa.String(length=160), nullable=True),
    )
    op.add_column(
        "webchat_handoff_requests",
        sa.Column("routing_owner", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "webchat_handoff_requests",
        sa.Column("routing_retry_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "webchat_handoff_requests",
        sa.Column("routing_exhausted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "webchat_handoff_requests",
        sa.Column("routing_policy_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "webchat_handoff_requests",
        sa.Column("routing_policy_json", sa.Text(), nullable=True),
    )
    op.add_column(
        "webchat_handoff_requests",
        sa.Column("routing_fallback_action", sa.String(length=80), nullable=True),
    )
    op.create_index(
        "ix_webchat_handoff_requests_routing_generation",
        "webchat_handoff_requests",
        ["routing_generation"],
        unique=False,
    )
    op.create_index(
        "ix_webchat_handoff_requests_routing_outcome",
        "webchat_handoff_requests",
        ["routing_outcome"],
        unique=False,
    )
    op.create_index(
        "ix_webchat_handoff_requests_routing_reason_code",
        "webchat_handoff_requests",
        ["routing_reason_code"],
        unique=False,
    )
    op.create_index(
        "ix_webchat_handoff_requests_routing_owner",
        "webchat_handoff_requests",
        ["routing_owner"],
        unique=False,
    )
    op.create_index(
        "ix_webchat_handoff_requests_routing_retry_at",
        "webchat_handoff_requests",
        ["routing_retry_at"],
        unique=False,
    )
    op.create_index(
        "ix_webchat_handoff_requests_routing_exhausted_at",
        "webchat_handoff_requests",
        ["routing_exhausted_at"],
        unique=False,
    )
    op.create_index(
        "ix_webchat_handoff_requests_routing_policy_sha256",
        "webchat_handoff_requests",
        ["routing_policy_sha256"],
        unique=False,
    )
    op.create_index(
        "ix_webchat_handoff_requests_routing_fallback_action",
        "webchat_handoff_requests",
        ["routing_fallback_action"],
        unique=False,
    )
    op.create_index(
        "ix_webchat_handoff_routing_outcome_retry",
        "webchat_handoff_requests",
        ["routing_outcome", "routing_retry_at", "requested_at"],
        unique=False,
    )

    bind.execute(
        sa.text(
            "UPDATE webchat_handoff_requests SET "
            "routing_outcome = CASE "
            "WHEN status = 'accepted' THEN 'accepted' "
            "WHEN status = 'requested' THEN 'waiting' "
            "ELSE 'fallback_selected' END, "
            "routing_owner = 'human_support'"
        )
    )

    op.add_column(
        "webchat_handoff_decisions",
        sa.Column(
            "routing_generation",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column(
        "webchat_handoff_decisions",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    bind.execute(
        sa.text(
            "UPDATE webchat_handoff_decisions "
            "SET expires_at = created_at WHERE expires_at IS NULL"
        )
    )
    op.create_index(
        "ix_webchat_handoff_decisions_routing_generation",
        "webchat_handoff_decisions",
        ["routing_generation"],
        unique=False,
    )
    op.create_index(
        "ix_webchat_handoff_decisions_expires_at",
        "webchat_handoff_decisions",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_webchat_handoff_decisions_request_actor_generation",
        "webchat_handoff_decisions",
        ["request_id", "actor_id", "routing_generation"],
        unique=False,
    )
    op.create_index(
        "ix_webchat_handoff_decisions_expiry",
        "webchat_handoff_decisions",
        ["expires_at", "decision"],
        unique=False,
    )

    # Defaults are an upgrade aid only. Application writes remain explicit.
    with op.batch_alter_table("webchat_handoff_requests") as batch:
        batch.alter_column(
            "routing_generation",
            existing_type=sa.Integer(),
            server_default=None,
        )
        batch.alter_column(
            "routing_outcome",
            existing_type=sa.String(length=40),
            server_default=None,
        )
    with op.batch_alter_table("webchat_handoff_decisions") as batch:
        batch.alter_column(
            "routing_generation",
            existing_type=sa.Integer(),
            server_default=None,
        )


def downgrade() -> None:
    op.drop_index(
        "ix_webchat_handoff_decisions_expiry",
        table_name="webchat_handoff_decisions",
    )
    op.drop_index(
        "ix_webchat_handoff_decisions_request_actor_generation",
        table_name="webchat_handoff_decisions",
    )
    op.drop_index(
        "ix_webchat_handoff_decisions_expires_at",
        table_name="webchat_handoff_decisions",
    )
    op.drop_index(
        "ix_webchat_handoff_decisions_routing_generation",
        table_name="webchat_handoff_decisions",
    )
    with op.batch_alter_table("webchat_handoff_decisions") as batch:
        batch.drop_column("expires_at")
        batch.drop_column("routing_generation")

    op.drop_index(
        "ix_webchat_handoff_routing_outcome_retry",
        table_name="webchat_handoff_requests",
    )
    op.drop_index(
        "ix_webchat_handoff_requests_routing_fallback_action",
        table_name="webchat_handoff_requests",
    )
    op.drop_index(
        "ix_webchat_handoff_requests_routing_policy_sha256",
        table_name="webchat_handoff_requests",
    )
    op.drop_index(
        "ix_webchat_handoff_requests_routing_exhausted_at",
        table_name="webchat_handoff_requests",
    )
    op.drop_index(
        "ix_webchat_handoff_requests_routing_retry_at",
        table_name="webchat_handoff_requests",
    )
    op.drop_index(
        "ix_webchat_handoff_requests_routing_owner",
        table_name="webchat_handoff_requests",
    )
    op.drop_index(
        "ix_webchat_handoff_requests_routing_reason_code",
        table_name="webchat_handoff_requests",
    )
    op.drop_index(
        "ix_webchat_handoff_requests_routing_outcome",
        table_name="webchat_handoff_requests",
    )
    op.drop_index(
        "ix_webchat_handoff_requests_routing_generation",
        table_name="webchat_handoff_requests",
    )
    with op.batch_alter_table("webchat_handoff_requests") as batch:
        batch.drop_column("routing_fallback_action")
        batch.drop_column("routing_policy_json")
        batch.drop_column("routing_policy_sha256")
        batch.drop_column("routing_exhausted_at")
        batch.drop_column("routing_retry_at")
        batch.drop_column("routing_owner")
        batch.drop_column("routing_reason_code")
        batch.drop_column("routing_outcome")
        batch.drop_column("routing_generation")
