"""Add immutable Case Scenario Assignment authority and backfill.

Revision ID: 20260728_r5_scenario
Revises: 20260728_r5_handoff
Create Date: 2026-07-28
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "20260728_r5_scenario"
down_revision = "20260728_r5_handoff"
branch_labels = None
depends_on = None


def _catalog() -> tuple[dict, str]:
    path = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "config"
        / "business_scenarios.v1.json"
    )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RuntimeError("case_scenario_backfill_catalog_unavailable") from exc
    payload = json.loads(raw.decode("utf-8"))
    if payload.get("schema") != "nexus.business-scenario-catalog.v1":
        raise RuntimeError("case_scenario_backfill_catalog_invalid")
    return payload, hashlib.sha256(raw).hexdigest()


def _snapshot(payload: dict, digest: str, scenario: dict) -> str:
    return json.dumps(
        {
            "schema": "nexus.case-scenario-assignment.v1",
            "catalog_version": payload["catalog_version"],
            "catalog_sha256": digest,
            "scenario": scenario,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def upgrade() -> None:
    op.create_table(
        "case_scenario_assignments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticket_id", sa.Integer(), nullable=False),
        sa.Column("scenario_key", sa.String(length=160), nullable=False),
        sa.Column("catalog_version", sa.String(length=120), nullable=False),
        sa.Column("catalog_sha256", sa.String(length=64), nullable=False),
        sa.Column("scenario_snapshot_json", sa.Text(), nullable=False),
        sa.Column("assignment_source", sa.String(length=80), nullable=False),
        sa.Column("assignment_reason", sa.Text(), nullable=True),
        sa.Column("assigned_by", sa.Integer(), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["tickets.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_id"],
            ["case_scenario_assignments.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for name, columns in (
        ("ix_case_scenario_assignments_ticket_id", ["ticket_id"]),
        ("ix_case_scenario_assignments_scenario_key", ["scenario_key"]),
        ("ix_case_scenario_assignments_assigned_by", ["assigned_by"]),
        ("ix_case_scenario_assignments_assigned_at", ["assigned_at"]),
        ("ix_case_scenario_assignments_superseded_at", ["superseded_at"]),
        ("ix_case_scenario_assignments_superseded_by_id", ["superseded_by_id"]),
        (
            "ix_case_scenario_assignments_catalog",
            ["catalog_version", "catalog_sha256"],
        ),
    ):
        op.create_index(name, "case_scenario_assignments", columns, unique=False)
    op.create_index(
        "uq_case_scenario_assignments_current_ticket",
        "case_scenario_assignments",
        ["ticket_id"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
        sqlite_where=sa.text("superseded_at IS NULL"),
    )
    op.create_index(
        "ix_case_scenario_assignments_current_scenario",
        "case_scenario_assignments",
        ["scenario_key", "assigned_at"],
        unique=False,
        postgresql_where=sa.text("superseded_at IS NULL"),
        sqlite_where=sa.text("superseded_at IS NULL"),
    )

    payload, digest = _catalog()
    scenarios = {
        row["scenario_key"]: row
        for row in payload.get("scenarios", [])
    }
    aliases: dict[str, str] = {}
    for key, row in scenarios.items():
        for alias in (key, *row.get("issue_type_aliases", [])):
            aliases[str(alias).strip().lower()] = key

    tickets = sa.table(
        "tickets",
        sa.column("id", sa.Integer),
        sa.column("case_type", sa.String),
        sa.column("sub_category", sa.String),
        sa.column("category", sa.String),
        sa.column("ai_classification", sa.String),
        sa.column("created_by", sa.Integer),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    assignments = sa.table(
        "case_scenario_assignments",
        sa.column("ticket_id", sa.Integer),
        sa.column("scenario_key", sa.String),
        sa.column("catalog_version", sa.String),
        sa.column("catalog_sha256", sa.String),
        sa.column("scenario_snapshot_json", sa.Text),
        sa.column("assignment_source", sa.String),
        sa.column("assignment_reason", sa.Text),
        sa.column("assigned_by", sa.Integer),
        sa.column("assigned_at", sa.DateTime(timezone=True)),
        sa.column("superseded_at", sa.DateTime(timezone=True)),
        sa.column("superseded_by_id", sa.Integer),
    )
    bind = op.get_bind()
    rows = bind.execute(sa.select(tickets)).mappings().all()
    resolved_rows: list[tuple[dict, str]] = []
    conflicts: list[dict] = []
    for ticket in rows:
        matches: dict[str, str] = {}
        for field in (
            "case_type",
            "sub_category",
            "category",
            "ai_classification",
        ):
            value = str(ticket[field] or "").strip().lower()
            if value and value in aliases:
                matches[field] = aliases[value]
        resolved = set(matches.values())
        if len(resolved) > 1:
            conflicts.append({"ticket_id": ticket["id"], "matches": matches})
        elif len(resolved) == 1:
            resolved_rows.append((ticket, next(iter(resolved))))

    if conflicts:
        raise RuntimeError(
            "case_scenario_backfill_conflict:"
            + json.dumps(conflicts[:20], sort_keys=True)
        )

    for ticket, key in resolved_rows:
        bind.execute(
            assignments.insert().values(
                ticket_id=int(ticket["id"]),
                scenario_key=key,
                catalog_version=payload["catalog_version"],
                catalog_sha256=digest,
                scenario_snapshot_json=_snapshot(payload, digest, scenarios[key]),
                assignment_source="legacy_backfill",
                assignment_reason="Resolved from historical Case identity aliases",
                assigned_by=ticket["created_by"],
                assigned_at=ticket["created_at"],
                superseded_at=None,
                superseded_by_id=None,
            )
        )


def downgrade() -> None:
    op.drop_table("case_scenario_assignments")
