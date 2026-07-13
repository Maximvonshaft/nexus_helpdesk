"""add Knowledge ingestion quarantine and audit authority

Revision ID: 20260713_0060
Revises: 20260713_0059
Create Date: 2026-07-13

The tables persist bounded safety metadata and audit transitions only. They do
not contain raw upload bytes, extracted text, prompts, customer data, scanner
payloads, credentials or parser stderr.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260713_0060"
down_revision = "20260713_0059"
branch_labels = None
depends_on = None

_RECORD_TABLE = "knowledge_ingestion_records"
_AUDIT_TABLE = "knowledge_ingestion_audit_events"

_RECORD_INDEXES: tuple[tuple[str, list[str]], ...] = (
    ("ix_knowledge_ingestion_records_knowledge_item_id", ["knowledge_item_id"]),
    ("ix_knowledge_ingestion_records_storage_key", ["storage_key"]),
    ("ix_knowledge_ingestion_records_content_sha256", ["content_sha256"]),
    ("ix_knowledge_ingestion_records_parsed_text_sha256", ["parsed_text_sha256"]),
    ("ix_knowledge_ingestion_records_signature_status", ["signature_status"]),
    ("ix_knowledge_ingestion_records_lifecycle_status", ["lifecycle_status"]),
    ("ix_knowledge_ingestion_records_parser_status", ["parser_status"]),
    ("ix_knowledge_ingestion_records_malware_status", ["malware_status"]),
    ("ix_knowledge_ingestion_records_cdr_status", ["cdr_status"]),
    ("ix_knowledge_ingestion_records_prompt_risk_status", ["prompt_risk_status"]),
    ("ix_knowledge_ingestion_records_source_trust", ["source_trust"]),
    ("ix_knowledge_ingestion_records_review_status", ["review_status"]),
    ("ix_knowledge_ingestion_records_created_by", ["created_by"]),
    ("ix_knowledge_ingestion_records_reviewed_by", ["reviewed_by"]),
    ("ix_knowledge_ingestion_records_reviewed_at", ["reviewed_at"]),
    ("ix_knowledge_ingestion_records_published_version", ["published_version"]),
    ("ix_knowledge_ingestion_records_published_at", ["published_at"]),
    ("ix_knowledge_ingestion_records_rolled_back_at", ["rolled_back_at"]),
    ("ix_knowledge_ingestion_records_created_at", ["created_at"]),
    ("ix_knowledge_ingestion_records_updated_at", ["updated_at"]),
    (
        "ix_knowledge_ingestion_item_lifecycle",
        ["knowledge_item_id", "lifecycle_status", "created_at"],
    ),
    (
        "ix_knowledge_ingestion_publication",
        ["knowledge_item_id", "published_version", "lifecycle_status"],
    ),
)

_AUDIT_INDEXES: tuple[tuple[str, list[str]], ...] = (
    ("ix_knowledge_ingestion_audit_events_ingestion_id", ["ingestion_id"]),
    ("ix_knowledge_ingestion_audit_events_event_type", ["event_type"]),
    ("ix_knowledge_ingestion_audit_events_actor_id", ["actor_id"]),
    ("ix_knowledge_ingestion_audit_events_created_at", ["created_at"]),
    (
        "ix_knowledge_ingestion_audit_sequence",
        ["ingestion_id", "created_at", "id"],
    ),
)


def upgrade() -> None:
    op.create_table(
        _RECORD_TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("knowledge_item_id", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("parsed_text_sha256", sa.String(length=64), nullable=True),
        sa.Column("sanitized_content_sha256", sa.String(length=64), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("declared_mime_type", sa.String(length=120), nullable=True),
        sa.Column("detected_mime_type", sa.String(length=120), nullable=True),
        sa.Column("signature_status", sa.String(length=40), nullable=False),
        sa.Column("lifecycle_status", sa.String(length=40), nullable=False),
        sa.Column("parser_status", sa.String(length=40), nullable=False),
        sa.Column("parser_identity", sa.String(length=120), nullable=True),
        sa.Column("parser_version", sa.String(length=80), nullable=True),
        sa.Column("malware_status", sa.String(length=40), nullable=False),
        sa.Column("cdr_status", sa.String(length=40), nullable=False),
        sa.Column("prompt_risk_status", sa.String(length=40), nullable=False),
        sa.Column("source_trust", sa.String(length=40), nullable=False),
        sa.Column("review_status", sa.String(length=40), nullable=False),
        sa.Column("safe_findings_json", sa.JSON(), nullable=True),
        sa.Column("rejection_reason", sa.String(length=120), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_version", sa.Integer(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "size_bytes > 0",
            name="ck_knowledge_ingestion_size_positive",
        ),
        sa.CheckConstraint(
            "length(content_sha256) = 64",
            name="ck_knowledge_ingestion_sha256_length",
        ),
        sa.CheckConstraint(
            "parsed_text_sha256 IS NULL OR length(parsed_text_sha256) = 64",
            name="ck_knowledge_ingestion_parsed_sha256_length",
        ),
        sa.CheckConstraint(
            "sanitized_content_sha256 IS NULL OR length(sanitized_content_sha256) = 64",
            name="ck_knowledge_ingestion_sanitized_sha256_length",
        ),
        sa.CheckConstraint(
            "lifecycle_status IN ('quarantined','parsing','review_required','approved','published','rejected','rolled_back')",
            name="ck_knowledge_ingestion_lifecycle_status",
        ),
        sa.CheckConstraint(
            "signature_status IN ('pending','match','mismatch','unsupported')",
            name="ck_knowledge_ingestion_signature_status",
        ),
        sa.CheckConstraint(
            "parser_status IN ('not_started','running','passed','failed','timed_out','resource_exceeded')",
            name="ck_knowledge_ingestion_parser_status",
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
            "review_status IN ('pending','approved','rejected','re_review_required')",
            name="ck_knowledge_ingestion_review_status",
        ),
        sa.CheckConstraint(
            "published_version IS NULL OR published_version > 0",
            name="ck_knowledge_ingestion_published_version_positive",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_item_id"],
            ["knowledge_items.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "knowledge_item_id",
            "content_sha256",
            name="uq_knowledge_ingestion_item_content",
        ),
        sa.UniqueConstraint(
            "storage_key",
            name="uq_knowledge_ingestion_storage_key",
        ),
    )
    for index_name, columns in _RECORD_INDEXES:
        op.create_index(index_name, _RECORD_TABLE, columns)

    op.create_table(
        _AUDIT_TABLE,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ingestion_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("from_status", sa.String(length=40), nullable=True),
        sa.Column("to_status", sa.String(length=40), nullable=True),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("reason_code", sa.String(length=120), nullable=False),
        sa.Column("safe_metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('quarantined','parse_started','parse_passed','parse_failed','scanner_recorded','review_approved','rejected','published','rolled_back','re_review_requested')",
            name="ck_knowledge_ingestion_audit_event_type",
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_id"],
            ["knowledge_ingestion_records.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for index_name, columns in _AUDIT_INDEXES:
        op.create_index(index_name, _AUDIT_TABLE, columns)


def downgrade() -> None:
    op.drop_table(_AUDIT_TABLE)
    op.drop_table(_RECORD_TABLE)
