"""audit reality closure hardening

Revision ID: 20260520_0026
Revises: 20260518_0025
Create Date: 2026-05-20
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260520_0026"
down_revision = "20260518_0025"
branch_labels = None
depends_on = None


BACKGROUND_JOB_ACTIVE_WHERE = "dedupe_key IS NOT NULL AND status IN ('pending', 'processing')"
OPENCLAW_UNRESOLVED_ACTIVE_WHERE = "payload_hash IS NOT NULL AND status IN ('pending', 'failed', 'replaying')"


def _dialect_name() -> str:
    return op.get_bind().dialect.name


def _inspector():
    return sa.inspect(op.get_bind())


def _table_exists(name: str) -> bool:
    return _inspector().has_table(name)


def _index_exists(name: str, table: str) -> bool:
    return name in {idx["name"] for idx in _inspector().get_indexes(table)}


def _drop_index_if_exists(name: str, table: str) -> None:
    if _index_exists(name, table):
        op.drop_index(name, table_name=table)


def _create_partial_unique_index_if_missing(name: str, table: str, columns: list[str], where_sql: str) -> None:
    if _index_exists(name, table):
        return
    kwargs = {"unique": True}
    dialect = _dialect_name()
    if dialect == "postgresql":
        kwargs["postgresql_where"] = sa.text(where_sql)
    elif dialect == "sqlite":
        kwargs["sqlite_where"] = sa.text(where_sql)
    else:
        return
    op.create_index(name, table, columns, **kwargs)


def _execute(statement: str, **params) -> None:
    op.get_bind().execute(sa.text(statement), params)


def _normalize_background_job_duplicates() -> None:
    dialect = _dialect_name()
    note = "db-level dedupe guard normalized duplicate active job"
    if dialect == "postgresql":
        _execute(
            """
                WITH ranked AS (
                    SELECT id,
                           row_number() OVER (
                               PARTITION BY dedupe_key
                               ORDER BY id ASC
                           ) AS rn
                    FROM background_jobs
                    WHERE dedupe_key IS NOT NULL
                      AND status IN ('pending', 'processing')
                )
                UPDATE background_jobs AS b
                SET status = 'failed',
                    last_error = :note,
                    updated_at = now()
                FROM ranked AS r
                WHERE b.id = r.id
                  AND r.rn > 1
                """,
            note=note,
        )
        return
    if dialect == "sqlite":
        _execute(
            """
                WITH ranked AS (
                    SELECT id,
                           row_number() OVER (
                               PARTITION BY dedupe_key
                               ORDER BY id ASC
                           ) AS rn
                    FROM background_jobs
                    WHERE dedupe_key IS NOT NULL
                      AND status IN ('pending', 'processing')
                )
                UPDATE background_jobs
                SET status = 'failed',
                    last_error = :note,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
                """,
            note=note,
        )



def _normalize_unresolved_duplicates() -> None:
    dialect = _dialect_name()
    note = "db-level unresolved-event idempotency guard normalized duplicate active row"
    if dialect == "postgresql":
        _execute(
            """
                WITH ranked AS (
                    SELECT id,
                           row_number() OVER (
                               PARTITION BY source, session_key, payload_hash
                               ORDER BY id ASC
                           ) AS rn
                    FROM openclaw_unresolved_events
                    WHERE payload_hash IS NOT NULL
                      AND status IN ('pending', 'failed', 'replaying')
                )
                UPDATE openclaw_unresolved_events AS e
                SET status = 'dropped_duplicate',
                    last_error = :note,
                    updated_at = now()
                FROM ranked AS r
                WHERE e.id = r.id
                  AND r.rn > 1
                """,
            note=note,
        )
        return
    if dialect == "sqlite":
        _execute(
            """
                WITH ranked AS (
                    SELECT id,
                           row_number() OVER (
                               PARTITION BY source, session_key, payload_hash
                               ORDER BY id ASC
                           ) AS rn
                    FROM openclaw_unresolved_events
                    WHERE payload_hash IS NOT NULL
                      AND status IN ('pending', 'failed', 'replaying')
                )
                UPDATE openclaw_unresolved_events
                SET status = 'dropped_duplicate',
                    last_error = :note,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
                """,
            note=note,
        )



def upgrade() -> None:
    if not _table_exists("admin_action_rate_limits"):
        op.create_table(
            "admin_action_rate_limits",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("bucket_key", sa.String(length=160), nullable=False),
            sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
            sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("bucket_key", name="ux_admin_action_rate_limits_bucket_key"),
        )
        op.create_index("ix_admin_action_rate_limits_window_start", "admin_action_rate_limits", ["window_start"], unique=False)

    _normalize_background_job_duplicates()
    _normalize_unresolved_duplicates()

    _create_partial_unique_index_if_missing(
        "uq_background_jobs_active_dedupe_key",
        "background_jobs",
        ["dedupe_key"],
        BACKGROUND_JOB_ACTIVE_WHERE,
    )
    _create_partial_unique_index_if_missing(
        "uq_openclaw_unresolved_active_payload_hash",
        "openclaw_unresolved_events",
        ["source", "session_key", "payload_hash"],
        OPENCLAW_UNRESOLVED_ACTIVE_WHERE,
    )



def downgrade() -> None:
    _drop_index_if_exists("uq_openclaw_unresolved_active_payload_hash", "openclaw_unresolved_events")
    _drop_index_if_exists("uq_background_jobs_active_dedupe_key", "background_jobs")
    if _table_exists("admin_action_rate_limits"):
        _drop_index_if_exists("ix_admin_action_rate_limits_window_start", "admin_action_rate_limits")
        op.drop_table("admin_action_rate_limits")
