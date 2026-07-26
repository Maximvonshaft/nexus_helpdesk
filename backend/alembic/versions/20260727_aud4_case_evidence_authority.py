"""Add structured Case evidence authority.

Revision ID: 20260727_aud4
Revises: 20260727_aud3
Create Date: 2026-07-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260727_aud4"
down_revision = "20260727_aud3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "case_evidence_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticket_id", sa.Integer(), nullable=False),
        sa.Column("evidence_kind", sa.String(length=24), nullable=False),
        sa.Column("evidence_key", sa.String(length=160), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("source_kind", sa.String(length=80), nullable=False),
        sa.Column("source_ref", sa.String(length=200), nullable=False),
        sa.Column("source_revision", sa.String(length=160), nullable=False),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=False),
        sa.Column("safe_metadata_json", sa.JSON(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["tickets.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "ticket_id",
            "evidence_key",
            "source_kind",
            "source_ref",
            "source_revision",
            name="uq_case_evidence_source_revision",
        ),
        sa.CheckConstraint(
            "evidence_kind IN ('fact','customer_input')",
            name="ck_case_evidence_kind",
        ),
        sa.CheckConstraint(
            "state IN ('verified','completed','waived','failed')",
            name="ck_case_evidence_state",
        ),
        sa.CheckConstraint(
            "length(trim(evidence_key)) > 0",
            name="ck_case_evidence_key_nonempty",
        ),
        sa.CheckConstraint(
            "length(trim(source_ref)) > 0 AND length(trim(source_revision)) > 0",
            name="ck_case_evidence_source_identity",
        ),
    )
    for name, columns in (
        ("ix_case_evidence_records_ticket_id", ["ticket_id"]),
        ("ix_case_evidence_records_evidence_kind", ["evidence_kind"]),
        ("ix_case_evidence_records_evidence_key", ["evidence_key"]),
        ("ix_case_evidence_records_state", ["state"]),
        ("ix_case_evidence_records_source_kind", ["source_kind"]),
        ("ix_case_evidence_records_evidence_sha256", ["evidence_sha256"]),
        ("ix_case_evidence_records_observed_at", ["observed_at"]),
        ("ix_case_evidence_records_recorded_by", ["recorded_by"]),
        ("ix_case_evidence_records_created_at", ["created_at"]),
        (
            "ix_case_evidence_ticket_kind_created",
            ["ticket_id", "evidence_kind", "created_at"],
        ),
        (
            "ix_case_evidence_source",
            ["source_kind", "source_ref"],
        ),
    ):
        op.create_index(name, "case_evidence_records", columns, unique=False)


def downgrade() -> None:
    op.drop_table("case_evidence_records")
