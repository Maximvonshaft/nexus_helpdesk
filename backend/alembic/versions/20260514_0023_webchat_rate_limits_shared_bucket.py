"""harden shared webchat rate limit buckets for production concurrency

Revision ID: 20260514_0023
Revises: 20260512_fl222
Create Date: 2026-05-14
"""

from __future__ import annotations

import hashlib

from alembic import op
import sqlalchemy as sa

revision = "20260514_0023"
down_revision = "20260512_fl222"
branch_labels = None
depends_on = None


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _indexes(bind, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return set()
    return {idx["name"] for idx in inspector.get_indexes(table_name)}


def _bucket_key_length(bind) -> int | None:
    inspector = sa.inspect(bind)
    for column in inspector.get_columns("webchat_rate_limits"):
        if column["name"] == "bucket_key":
            return getattr(column["type"], "length", None)
    return None


def _drop_bucket_indexes(bind) -> None:
    indexes = _indexes(bind, "webchat_rate_limits")
    if "ix_webchat_rate_limits_bucket_key" in indexes:
        op.drop_index("ix_webchat_rate_limits_bucket_key", table_name="webchat_rate_limits")
    if "ux_webchat_rate_limits_bucket_key" in indexes:
        op.drop_index("ux_webchat_rate_limits_bucket_key", table_name="webchat_rate_limits")


def _normalize_bucket_keys(bind) -> None:
    rows = bind.execute(sa.text("SELECT id, bucket_key FROM webchat_rate_limits")).mappings().all()
    for row in rows:
        bucket_key = row["bucket_key"] or ""
        if len(bucket_key) == 64:
            continue
        bind.execute(
            sa.text("UPDATE webchat_rate_limits SET bucket_key = :bucket_key WHERE id = :id"),
            {
                "id": row["id"],
                "bucket_key": hashlib.sha256(bucket_key.encode("utf-8")).hexdigest(),
            },
        )


def _dedupe_bucket_keys() -> None:
    op.execute(
        sa.text(
            """
            DELETE FROM webchat_rate_limits
            WHERE id IN (
                SELECT id FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY bucket_key
                               ORDER BY updated_at DESC, id DESC
                           ) AS row_num
                    FROM webchat_rate_limits
                ) ranked
                WHERE ranked.row_num > 1
            )
            """
        )
    )


def _shrink_bucket_key_column(bind) -> None:
    existing_length = _bucket_key_length(bind)
    if existing_length == 64:
        return
    existing_type = sa.String(length=existing_length or 255)
    if bind.dialect.name == "postgresql":
        op.alter_column("webchat_rate_limits", "bucket_key", existing_type=existing_type, type_=sa.String(length=64), existing_nullable=False)
        return
    with op.batch_alter_table("webchat_rate_limits") as batch_op:
        batch_op.alter_column("bucket_key", existing_type=existing_type, type_=sa.String(length=64), existing_nullable=False)


def upgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)
    if "webchat_rate_limits" not in tables:
        op.create_table(
            "webchat_rate_limits",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("bucket_key", sa.String(length=64), nullable=False),
            sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
            sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    else:
        _drop_bucket_indexes(bind)
        _normalize_bucket_keys(bind)
        _dedupe_bucket_keys()
        _shrink_bucket_key_column(bind)
    indexes = _indexes(bind, "webchat_rate_limits")
    if "ux_webchat_rate_limits_bucket_key" not in indexes:
        op.create_index("ux_webchat_rate_limits_bucket_key", "webchat_rate_limits", ["bucket_key"], unique=True)
    if "ix_webchat_rate_limits_window_start" not in indexes:
        op.create_index("ix_webchat_rate_limits_window_start", "webchat_rate_limits", ["window_start"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if "webchat_rate_limits" not in _tables(bind):
        return
    indexes = _indexes(bind, "webchat_rate_limits")
    if "ix_webchat_rate_limits_window_start" in indexes:
        op.drop_index("ix_webchat_rate_limits_window_start", table_name="webchat_rate_limits")
    if "ix_webchat_rate_limits_bucket_key" in indexes:
        op.drop_index("ix_webchat_rate_limits_bucket_key", table_name="webchat_rate_limits")
    if "ux_webchat_rate_limits_bucket_key" in indexes:
        op.drop_index("ux_webchat_rate_limits_bucket_key", table_name="webchat_rate_limits")
    op.drop_table("webchat_rate_limits")
