"""persona profile review workflow

Revision ID: 20260529_0041
Revises: 20260529_0040
Create Date: 2026-05-29
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260529_0041"
down_revision = "20260529_0040"
branch_labels = None
depends_on = None


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _indexes(bind, table_name: str) -> set[str]:
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return set()
    return {idx["name"] for idx in inspector.get_indexes(table_name)}


def _create_index_once(bind, name: str, columns: list[str]) -> None:
    if name not in _indexes(bind, "persona_profile_reviews"):
        op.create_index(name, "persona_profile_reviews", columns, unique=False)


def upgrade() -> None:
    bind = op.get_bind()
    if "persona_profiles" not in _tables(bind):
        return

    if "persona_profile_reviews" not in _tables(bind):
        op.create_table(
            "persona_profile_reviews",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("profile_id", sa.Integer(), sa.ForeignKey("persona_profiles.id"), nullable=False),
            sa.Column("review_version", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="pending"),
            sa.Column("snapshot_json", sa.JSON(), nullable=False),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("requested_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("decision_note", sa.Text(), nullable=True),
            sa.Column("release_window_start", sa.DateTime(timezone=True), nullable=True),
            sa.Column("release_window_end", sa.DateTime(timezone=True), nullable=True),
            sa.Column("published_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("published_version", sa.Integer(), nullable=True),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("profile_id", "review_version", name="uq_persona_profile_review_version"),
        )

    for name, columns in [
        ("ix_persona_profile_reviews_profile_id", ["profile_id"]),
        ("ix_persona_profile_reviews_review_version", ["review_version"]),
        ("ix_persona_profile_reviews_status", ["status"]),
        ("ix_persona_profile_reviews_requested_by", ["requested_by"]),
        ("ix_persona_profile_reviews_requested_at", ["requested_at"]),
        ("ix_persona_profile_reviews_reviewed_by", ["reviewed_by"]),
        ("ix_persona_profile_reviews_reviewed_at", ["reviewed_at"]),
        ("ix_persona_profile_reviews_release_window_start", ["release_window_start"]),
        ("ix_persona_profile_reviews_release_window_end", ["release_window_end"]),
        ("ix_persona_profile_reviews_published_by", ["published_by"]),
        ("ix_persona_profile_reviews_published_version", ["published_version"]),
        ("ix_persona_profile_reviews_published_at", ["published_at"]),
        ("ix_persona_profile_reviews_created_at", ["created_at"]),
        ("ix_persona_profile_reviews_updated_at", ["updated_at"]),
    ]:
        _create_index_once(bind, name, columns)


def downgrade() -> None:
    bind = op.get_bind()
    if "persona_profile_reviews" not in _tables(bind):
        return

    for name in [
        "ix_persona_profile_reviews_updated_at",
        "ix_persona_profile_reviews_created_at",
        "ix_persona_profile_reviews_published_at",
        "ix_persona_profile_reviews_published_version",
        "ix_persona_profile_reviews_published_by",
        "ix_persona_profile_reviews_release_window_end",
        "ix_persona_profile_reviews_release_window_start",
        "ix_persona_profile_reviews_reviewed_at",
        "ix_persona_profile_reviews_reviewed_by",
        "ix_persona_profile_reviews_requested_at",
        "ix_persona_profile_reviews_requested_by",
        "ix_persona_profile_reviews_status",
        "ix_persona_profile_reviews_review_version",
        "ix_persona_profile_reviews_profile_id",
    ]:
        if name in _indexes(bind, "persona_profile_reviews"):
            op.drop_index(name, table_name="persona_profile_reviews")

    op.drop_table("persona_profile_reviews")
