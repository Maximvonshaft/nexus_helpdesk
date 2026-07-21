"""add canonical Agent Run lifecycle and append-only events

Revision ID: 20260721_0070
Revises: 20260721_0069
Create Date: 2026-07-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260721_0070"
down_revision = "20260721_0069"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("request_id", sa.String(length=160), nullable=False),
        sa.Column("session_id", sa.String(length=160), nullable=False),
        sa.Column("tenant_key", sa.String(length=80), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column(
            "deployment_id",
            sa.Integer(),
            sa.ForeignKey("agent_deployments.id"),
            nullable=True,
        ),
        sa.Column(
            "release_id",
            sa.Integer(),
            sa.ForeignKey("agent_releases.id"),
            nullable=True,
        ),
        sa.Column("release_digest", sa.String(length=64), nullable=True),
        sa.Column(
            "parent_run_id",
            sa.Integer(),
            sa.ForeignKey("agent_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("fork_kind", sa.String(length=24), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="running"),
        sa.Column("final_action", sa.String(length=80), nullable=True),
        sa.Column("error_code", sa.String(length=160), nullable=True),
        sa.Column("elapsed_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("request_id", name="uq_agent_run_request"),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'fallback', 'failed', 'cancelled')",
            name="ck_agent_run_status",
        ),
        sa.CheckConstraint(
            "fork_kind IS NULL OR fork_kind IN ('playground', 'replay')",
            name="ck_agent_run_fork_kind",
        ),
        sa.CheckConstraint("elapsed_ms >= 0", name="ck_agent_run_elapsed_nonnegative"),
    )
    for column in (
        "request_id",
        "session_id",
        "tenant_key",
        "trace_id",
        "deployment_id",
        "release_id",
        "release_digest",
        "parent_run_id",
        "fork_kind",
        "status",
        "final_action",
        "error_code",
        "started_at",
        "completed_at",
    ):
        op.create_index(f"ix_agent_runs_{column}", "agent_runs", [column])
    op.create_index(
        "ix_agent_runs_tenant_started",
        "agent_runs",
        ["tenant_key", "started_at"],
    )
    op.create_index(
        "ix_agent_runs_session_started",
        "agent_runs",
        ["session_id", "started_at"],
    )
    op.create_index(
        "ix_agent_runs_release_status",
        "agent_runs",
        ["release_id", "status"],
    )

    op.create_table(
        "agent_run_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "run_id",
            sa.Integer(),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("round_index", sa.Integer(), nullable=True),
        sa.Column(
            "parent_event_id",
            sa.Integer(),
            sa.ForeignKey("agent_run_events.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="recorded"),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("safe_payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "sequence", name="uq_agent_run_event_sequence"),
        sa.CheckConstraint("sequence > 0", name="ck_agent_run_event_sequence_positive"),
        sa.CheckConstraint(
            "duration_ms >= 0", name="ck_agent_run_event_duration_nonnegative"
        ),
    )
    for column in (
        "run_id",
        "event_type",
        "round_index",
        "parent_event_id",
        "status",
        "created_at",
    ):
        op.create_index(
            f"ix_agent_run_events_{column}", "agent_run_events", [column]
        )
    op.create_index(
        "ix_agent_run_events_run_created",
        "agent_run_events",
        ["run_id", "created_at"],
    )
    op.create_index(
        "ix_agent_run_events_type_created",
        "agent_run_events",
        ["event_type", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("agent_run_events")
    op.drop_table("agent_runs")
