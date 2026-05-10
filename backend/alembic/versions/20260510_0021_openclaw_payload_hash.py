"""openclaw unresolved payload hash

Revision ID: 20260510_0021
Revises: 20260507_0020
Create Date: 2026-05-10
"""

from __future__ import annotations

import hashlib
import json

from alembic import op
import sqlalchemy as sa

revision = "20260510_0021"
down_revision = "20260507_0020"
branch_labels = None
depends_on = None


def _canonical_payload_hash(payload_json: str | None) -> str:
    try:
        parsed = json.loads(payload_json or "{}")
    except Exception:
        parsed = payload_json or ""
    canonical = json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def upgrade() -> None:
    op.add_column("openclaw_unresolved_events", sa.Column("payload_hash", sa.String(length=64), nullable=True))

    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, payload_json FROM openclaw_unresolved_events WHERE payload_hash IS NULL")).fetchall()
    for row in rows:
        bind.execute(
            sa.text("UPDATE openclaw_unresolved_events SET payload_hash = :payload_hash WHERE id = :id"),
            {"payload_hash": _canonical_payload_hash(row.payload_json), "id": row.id},
        )

    op.alter_column("openclaw_unresolved_events", "payload_hash", nullable=False)
    op.create_index(
        "ix_openclaw_unresolved_payload_hash_status",
        "openclaw_unresolved_events",
        ["source", "session_key", "payload_hash", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_openclaw_unresolved_payload_hash_status", table_name="openclaw_unresolved_events")
    op.drop_column("openclaw_unresolved_events", "payload_hash")
