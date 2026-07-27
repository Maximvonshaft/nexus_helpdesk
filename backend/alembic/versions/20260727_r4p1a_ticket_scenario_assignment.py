"""Create and safely backfill canonical Ticket Scenario assignments.

Revision ID: 20260727_r4p1a
Revises: 20260727_r4p0c
Create Date: 2026-07-27
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "20260727_r4p1a"
down_revision = "20260727_r4p0c"
branch_labels = None
depends_on = None


def _canonical_json(value) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _catalog_payload() -> tuple[dict, dict[str, str], dict[str, dict]]:
    path = Path(__file__).resolve().parents[2] / "app" / "config" / "business_scenarios.v1.json"
    raw = path.read_bytes()
    payload = json.loads(raw.decode("utf-8"))
    aliases: dict[str, str] = {}
    definitions: dict[str, dict] = {}
    for row in payload["scenarios"]:
        key = str(row["scenario_key"]).strip().lower()
        definitions[key] = row
        for alias in (key, *row.get("issue_type_aliases", [])):
            normalized = str(alias).strip().lower()
            existing = aliases.get(normalized)
            if existing is not None and existing != key:
                raise RuntimeError(
                    f"scenario_catalog_alias_conflict:{normalized}:{existing}:{key}"
                )
            aliases[normalized] = key
    return payload, aliases, definitions


def upgrade() -> None:
    op.create_table(
        "ticket_scenario_assignments",
        sa.Column("ticket_id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=True),
        sa.Column("scenario_key", sa.String(length=160), nullable=False),
        sa.Column("assignment_revision", sa.Integer(), nullable=False),
        sa.Column("catalog_version", sa.String(length=80), nullable=False),
        sa.Column("catalog_sha256", sa.String(length=64), nullable=False),
        sa.Column("definition_sha256", sa.String(length=64), nullable=False),
        sa.Column("definition_json", sa.JSON(), nullable=False),
        sa.Column("assignment_source", sa.String(length=40), nullable=False),
        sa.Column("assignment_reason", sa.Text(), nullable=True),
        sa.Column("assigned_by", sa.Integer(), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["assigned_by"], ["users.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["tickets.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("ticket_id"),
    )
    op.create_index(
        "ix_ticket_scenario_assignments_tenant_id",
        "ticket_scenario_assignments",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_ticket_scenario_assignments_scenario_key",
        "ticket_scenario_assignments",
        ["scenario_key"],
        unique=False,
    )
    op.create_index(
        "ix_ticket_scenario_assignments_assigned_by",
        "ticket_scenario_assignments",
        ["assigned_by"],
        unique=False,
    )
    op.create_index(
        "ix_ticket_scenario_assignments_assigned_at",
        "ticket_scenario_assignments",
        ["assigned_at"],
        unique=False,
    )
    op.create_index(
        "ix_ticket_scenario_assignment_tenant_key",
        "ticket_scenario_assignments",
        ["tenant_id", "scenario_key", "assigned_at"],
        unique=False,
    )
    op.create_index(
        "ix_ticket_scenario_assignment_catalog",
        "ticket_scenario_assignments",
        ["catalog_version", "catalog_sha256"],
        unique=False,
    )

    payload, aliases, definitions = _catalog_payload()
    catalog_sha = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    rows = op.get_bind().execute(
        sa.text(
            "SELECT id, tenant_id, case_type, sub_category, category, ai_classification "
            "FROM tickets ORDER BY id"
        )
    ).mappings()
    for row in rows:
        matched: set[str] = set()
        observed: list[str] = []
        for column in ("case_type", "sub_category", "category", "ai_classification"):
            value = str(row[column] or "").strip().lower()
            if not value:
                continue
            observed.append(f"{column}:{value}")
            target = aliases.get(value)
            if target is not None:
                matched.add(target)
        if len(matched) > 1:
            raise RuntimeError(
                "ticket_scenario_backfill_conflict:"
                f"ticket={row['id']}:matches={','.join(sorted(matched))}:"
                f"observed={'|'.join(observed)}"
            )
        if len(matched) != 1:
            continue
        scenario_key = next(iter(matched))
        definition = definitions[scenario_key]
        definition_json = _canonical_json(definition)
        definition_sha = hashlib.sha256(definition_json.encode("utf-8")).hexdigest()
        op.get_bind().execute(
            sa.text(
                "INSERT INTO ticket_scenario_assignments ("
                " ticket_id, tenant_id, scenario_key, assignment_revision,"
                " catalog_version, catalog_sha256, definition_sha256, definition_json,"
                " assignment_source, assignment_reason, assigned_by, assigned_at, updated_at"
                ") VALUES ("
                " :ticket_id, :tenant_id, :scenario_key, 1,"
                " :catalog_version, :catalog_sha256, :definition_sha256, :definition_json,"
                " 'migration_backfill', :assignment_reason, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP"
                ")"
            ),
            {
                "ticket_id": row["id"],
                "tenant_id": row["tenant_id"],
                "scenario_key": scenario_key,
                "catalog_version": payload["catalog_version"],
                "catalog_sha256": catalog_sha,
                "definition_sha256": definition_sha,
                "definition_json": definition_json,
                "assignment_reason": "resolved aliases: " + ", ".join(observed),
            },
        )


def downgrade() -> None:
    for name in (
        "ix_ticket_scenario_assignment_catalog",
        "ix_ticket_scenario_assignment_tenant_key",
        "ix_ticket_scenario_assignments_assigned_at",
        "ix_ticket_scenario_assignments_assigned_by",
        "ix_ticket_scenario_assignments_scenario_key",
        "ix_ticket_scenario_assignments_tenant_id",
    ):
        op.drop_index(name, table_name="ticket_scenario_assignments")
    op.drop_table("ticket_scenario_assignments")
