"""add governed Knowledge ingestion quarantine

Revision ID: 20260712_0059
Revises: 20260711_0058
Create Date: 2026-07-12

The table stores bounded ingestion safety metadata only. It contains no raw file
bytes, extracted text, customer message, provider payload or credential.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260712_0059"
down_revision = "20260711_0058"
branch_labels = None
depends_on = None

_TABLE = "knowledge_ingestion_records"
_INDEXES = (
    ("ix_knowledge_ingestion_records_knowledge_item_id", ["knowledge_item_id"]),
    ("ix_knowledge_ingestion_records_tenant_key", ["tenant_key"]),
    ("ix_knowledge_ingestion_records_storage_key", ["storage_key"]),
    ("ix_knowledge_ingestion_records_content_sha256", ["content_sha256"]),
    ("ix_knowledge_ingestion_records_signature_status", ["signature_status"]),
    ("ix_knowledge_ingestion_records_lifecycle_status", ["lifecycle_status"]),
    ("ix_knowledge_ingestion_records_malware_status", ["malware_status"]),
    ("ix_knowledge_ingestion_records_cdr_status", ["cdr_status"]),
    ("ix_knowledge_ingestion_records_prompt_risk_status", ["prompt_risk_status"]),
    ("ix_knowledge_ingestion_records_source_trust", ["source_trust"]),
    ("ix_knowledge_ingestion_records_review_status", ["review_status"]),
    ("ix_knowledge_ingestion_records_created_by", ["created_by"]),
    ("ix_knowledge_ingestion_records_reviewed_by", ["reviewed_by"]),
    ("ix_knowledge_ingestion_records_scanned_at", ["scanned_at"]),
    ("ix_knowledge_ingestion_records_reviewed_at", ["reviewed_at"]),
    ("ix_knowledge_ingestion_records_created_at", ["created_at"]),
    ("ix_knowledge_ingestion_records_updated_at", ["updated_at"]),
    ("ix_knowledge_ingestion_scope_status", ["tenant_key", "lifecycle_status", "created_at"]),
    ("ix_knowledge_ingestion_item_status", ["knowledge_item_id", "lifecycle_status"]),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE in inspector.get_table_names():
        return
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("knowledge_item_id", sa.Integer(), nullable=False),
        sa.Column("tenant_key", sa.String(length=80), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("declared_mime_type", sa.String(length=120), nullable=True),
        sa.Column("detected_media_type", sa.String(length=120), nullable=True),
        sa.Column("signature_status", sa.String(length=40), nullable=False, server_default="pending"),
        sa.Column("lifecycle_status", sa.String(length=40), nullable=False, server_default="quarantined"),
        sa.Column("malware_status", sa.String(length=40), nullable=False, server_default="unavailable"),
        sa.Column("cdr_status", sa.String(length=40), nullable=False, server_default="unavailable"),
        sa.Column("prompt_risk_status", sa.String(length=40), nullable=False, server_default="pending"),
        sa.Column("source_trust", sa.String(length=40), nullable=False, server_default="untrusted"),
        sa.Column("review_status", sa.String(length=40), nullable=False, server_default="pending"),
        sa.Column("parser_name", sa.String(length=80), nullable=True),
        sa.Column("parser_version", sa.String(length=80), nullable=True),
        sa.Column("safe_findings_json", sa.JSON(), nullable=True),
        sa.Column("rejection_reason", sa.String(length=120), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint("size_bytes >= 0", name="ck_knowledge_ingestion_size_nonnegative"),
        sa.CheckConstraint("length(content_sha256) = 64", name="ck_knowledge_ingestion_sha256_length"),
        sa.CheckConstraint(
            "lifecycle_status IN ('quarantined','scanning','review_required','approved','rejected','superseded')",
            name="ck_knowledge_ingestion_lifecycle_status",
        ),
        sa.CheckConstraint(
            "signature_status IN ('pending','match','mismatch','unsupported')",
            name="ck_knowledge_ingestion_signature_status",
        ),
        sa.CheckConstraint(
            "malware_status IN ('unavailable','pending','clean','malicious','error')",
            name="ck_knowledge_ingestion_malware_status",
        ),
        sa.CheckConstraint(
            "cdr_status IN ('unavailable','pending','clean','sanitized','rejected','error')",
            name="ck_knowledge_ingestion_cdr_status",
        ),
        sa.CheckConstraint(
            "prompt_risk_status IN ('pending','clear','review','blocked')",
            name="ck_knowledge_ingestion_prompt_risk_status",
        ),
        sa.CheckConstraint(
            "source_trust IN ('untrusted','internal_unreviewed','internal_reviewed','external_verified')",
            name="ck_knowledge_ingestion_source_trust",
        ),
        sa.CheckConstraint(
            "review_status IN ('pending','approved','rejected')",
            name="ck_knowledge_ingestion_review_status",
        ),
        sa.ForeignKeyConstraint(["knowledge_item_id"], ["knowledge_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("knowledge_item_id", "content_sha256", name="uq_knowledge_ingestion_item_content"),
        sa.UniqueConstraint("tenant_key", "storage_key", name="uq_knowledge_ingestion_tenant_storage"),
    )
    for index_name, columns in _INDEXES:
        op.create_index(index_name, _TABLE, columns)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in inspector.get_table_names():
        return
    existing = {item["name"] for item in inspector.get_indexes(_TABLE)}
    for index_name, _columns in reversed(_INDEXES):
        if index_name in existing:
            op.drop_index(index_name, table_name=_TABLE)
    op.drop_table(_TABLE)
