"""Backfill immutable SLA revisions, assignments and query targets.

Revision ID: 20260727_aud6
Revises: 20260727_aud5
Create Date: 2026-07-27
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from alembic import op

revision = "20260727_aud6"
down_revision = "20260727_aud5"
branch_labels = None
depends_on = None

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def upgrade() -> None:
    op.create_table(
        "ticket_sla_targets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticket_id", sa.Integer(), nullable=False),
        sa.Column("assignment_id", sa.Integer(), nullable=False),
        sa.Column("first_response_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolution_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_response_risk_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolution_risk_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paused_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_revision", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["assignment_id"],
            ["ticket_sla_assignments.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("ticket_id", name="uq_ticket_sla_target_ticket"),
    )
    for name, columns in (
        ("ix_ticket_sla_targets_ticket_id", ["ticket_id"]),
        ("ix_ticket_sla_targets_assignment_id", ["assignment_id"]),
        ("ix_ticket_sla_targets_first_response_due_at", ["first_response_due_at"]),
        ("ix_ticket_sla_targets_resolution_due_at", ["resolution_due_at"]),
        ("ix_ticket_sla_targets_first_response_risk_at", ["first_response_risk_at"]),
        ("ix_ticket_sla_targets_resolution_risk_at", ["resolution_risk_at"]),
        ("ix_ticket_sla_targets_calculated_at", ["calculated_at"]),
        ("ix_ticket_sla_target_first_risk", ["first_response_risk_at", "ticket_id"]),
        ("ix_ticket_sla_target_resolution_risk", ["resolution_risk_at", "ticket_id"]),
    ):
        op.create_index(name, "ticket_sla_targets", columns, unique=False)

    bind = op.get_bind()
    metadata = sa.MetaData()
    policies = sa.Table("sla_policies", metadata, autoload_with=bind)
    revisions = sa.Table("sla_policy_revisions", metadata, autoload_with=bind)
    tickets = sa.Table("tickets", metadata, autoload_with=bind)
    assignments = sa.Table("ticket_sla_assignments", metadata, autoload_with=bind)
    targets = sa.Table("ticket_sla_targets", metadata, autoload_with=bind)

    revision_by_policy: dict[int, tuple[int, dict]] = {}
    now = datetime.now(timezone.utc)
    for policy in bind.execute(sa.select(policies).order_by(policies.c.id.asc())).mappings():
        existing = bind.execute(
            sa.select(revisions).where(
                revisions.c.policy_id == policy["id"],
                revisions.c.version == 1,
            )
        ).mappings().first()
        snapshot = {
            "schema": "nexus.sla-assignment.v1",
            "policy_id": int(policy["id"]),
            "policy_version": 1,
            "priority": str(policy["priority"]),
            "timezone_name": "UTC",
            "weekly_schedule": {},
            "holidays": [],
            "first_response_minutes": int(policy["first_response_minutes"]),
            "resolution_minutes": int(policy["resolution_minutes"]),
            "action_minutes": None,
            "notification_minutes": None,
            "risk_window_minutes": 30,
            "pause_reasons": [
                reason
                for reason, enabled in (
                    ("waiting_customer", bool(policy["pause_on_waiting_customer"])),
                    ("waiting_internal", bool(policy["pause_on_waiting_internal"])),
                )
                if enabled
            ],
            "scope": {"global_template": True},
        }
        if existing is None:
            result = bind.execute(
                revisions.insert().values(
                    policy_id=int(policy["id"]),
                    version=1,
                    tenant_id=None,
                    is_global_template=True,
                    market_id=None,
                    channel_key=None,
                    scenario_key=None,
                    customer_tier=None,
                    status="approved",
                    timezone_name="UTC",
                    weekly_schedule_json={},
                    holidays_json=[],
                    first_response_minutes=snapshot["first_response_minutes"],
                    resolution_minutes=snapshot["resolution_minutes"],
                    action_minutes=None,
                    notification_minutes=None,
                    risk_window_minutes=30,
                    pause_reasons_json=snapshot["pause_reasons"],
                    effective_from=_EPOCH,
                    effective_to=None,
                    approved_by=None,
                    created_at=now,
                )
            )
            revision_id = int(result.inserted_primary_key[0])
        else:
            revision_id = int(existing["id"])
        revision_by_policy[int(policy["id"])] = (revision_id, snapshot)

    policy_by_priority = {
        str(row["priority"]): int(row["id"])
        for row in bind.execute(sa.select(policies)).mappings()
    }
    for ticket in bind.execute(sa.select(tickets).order_by(tickets.c.id.asc())).mappings():
        policy_id = ticket["sla_policy_id"]
        if policy_id is None:
            policy_id = policy_by_priority.get(str(ticket["priority"]))
            if policy_id is None:
                continue
            bind.execute(
                tickets.update()
                .where(tickets.c.id == ticket["id"])
                .values(sla_policy_id=policy_id)
            )
        revision = revision_by_policy.get(int(policy_id))
        if revision is None:
            continue
        revision_id, snapshot = revision
        assignment = bind.execute(
            sa.select(assignments).where(
                assignments.c.ticket_id == ticket["id"]
            )
        ).mappings().first()
        if assignment is None:
            result = bind.execute(
                assignments.insert().values(
                    ticket_id=int(ticket["id"]),
                    policy_revision_id=revision_id,
                    snapshot_json=snapshot,
                    assigned_at=ticket["created_at"] or now,
                    assigned_by=ticket["created_by"],
                )
            )
            assignment_id = int(result.inserted_primary_key[0])
        else:
            assignment_id = int(assignment["id"])
            snapshot = assignment["snapshot_json"] or snapshot

        base = ticket["created_at"] or now
        first_due = ticket["first_response_due_at"] or (
            base + timedelta(minutes=int(snapshot["first_response_minutes"]))
        )
        resolution_due = ticket["resolution_due_at"] or (
            base + timedelta(minutes=int(snapshot["resolution_minutes"]))
        )
        risk_minutes = int(snapshot.get("risk_window_minutes") or 0)
        bind.execute(
            targets.insert().values(
                ticket_id=int(ticket["id"]),
                assignment_id=assignment_id,
                first_response_due_at=first_due,
                resolution_due_at=resolution_due,
                first_response_risk_at=first_due - timedelta(minutes=risk_minutes),
                resolution_risk_at=resolution_due - timedelta(minutes=risk_minutes),
                paused_seconds=int(ticket["total_paused_seconds"] or 0),
                calculated_at=now,
                updated_at=now,
                source_revision=int(snapshot.get("policy_version") or 1),
            )
        )


def downgrade() -> None:
    op.drop_table("ticket_sla_targets")
