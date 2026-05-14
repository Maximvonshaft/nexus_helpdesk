"""harden shared webchat rate limit buckets for production concurrency

Revision ID: 20260514_0023
Revises: 20260512_fl222
Create Date: 2026-05-14
"""

from __future__ import annotations

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
    _dedupe_bucket_keys()
    indexes = _indexes(bind, "webchat_rate_limits")
    if "ix_webchat_rate_limits_bucket_key" in indexes:
        op.drop_index("ix_webchat_rate_limits_bucket_key", table_name="webchat_rate_limits")
        indexes.remove("ix_webchat_rate_limits_bucket_key")
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
