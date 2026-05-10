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
from sqlalchemy import inspect

revision = "20260510_0021"
down_revision = "20260507_0020"
branch_labels = None
depends_on = None

INDEX_NAME = "ix_openclaw_unresolved_payload_hash_status"
TABLE_NAME = "openclaw_unresolved_events"


def _canonical_payload_hash(payload_json: str | None) -> str:
    try:
        parsed = json.loads(payload_json or "{}")
    except Exception:
        parsed = payload_json or ""
    canonical = json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _has_column(bind, column_name: str) -> bool:
    return column_name in {col["name"] for col in inspect(bind).get_columns(TABLE_NAME)}


def _has_index(bind, index_name: str) -> bool:
    return index_name in {idx["name"] for idx in inspect(bind).get_indexes(TABLE_NAME)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "payload_hash"):
        op.add_column(TABLE_NAME, sa.Column("payload_hash", sa.String(length=64), nullable=True))

    rows = bind.execute(sa.text("SELECT id, payload_json FROM openclaw_unresolved_events WHERE payload_hash IS NULL")).fetchall()
    for row in rows:
        bind.execute(
            sa.text("UPDATE openclaw_unresolved_events SET payload_hash = :payload_hash WHERE id = :id"),
            {"payload_hash": _canonical_payload_hash(row.payload_json), "id": row.id},
        )

    if bind.dialect.name != "sqlite":
        op.alter_column(TABLE_NAME, "payload_hash", nullable=False)
    if not _has_index(bind, INDEX_NAME):
        op.create_index(
            INDEX_NAME,
            TABLE_NAME,
            ["source", "session_key", "payload_hash", "status"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_index(bind, INDEX_NAME):
        op.drop_index(INDEX_NAME, table_name=TABLE_NAME)
    if _has_column(bind, "payload_hash"):
        op.drop_column(TABLE_NAME, "payload_hash")
