from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .utils.time import utc_now

UTCDateTime = DateTime(timezone=True)


class KnowledgeIngestionRecord(Base):
    """Server-owned safety authority for one uploaded Knowledge artifact.

    Only bounded metadata is persisted. Raw bytes, extracted text, prompt text,
    customer data, credentials, scanner payloads and parser stderr are forbidden.
    Publication eligibility is derived by policy and is never caller-controlled.
    """

    __tablename__ = "knowledge_ingestion_records"
    __table_args__ = (
        UniqueConstraint(
            "knowledge_item_id",
            "content_sha256",
            name="uq_knowledge_ingestion_item_content",
        ),
        UniqueConstraint("storage_key", name="uq_knowledge_ingestion_storage_key"),
        CheckConstraint("size_bytes > 0", name="ck_knowledge_ingestion_size_positive"),
        CheckConstraint(
            "length(content_sha256) = 64",
            name="ck_knowledge_ingestion_sha256_length",
        ),
        CheckConstraint(
            "sanitized_content_sha256 IS NULL OR length(sanitized_content_sha256) = 64",
            name="ck_knowledge_ingestion_sanitized_sha256_length",
        ),
        CheckConstraint(
            "lifecycle_status IN ('quarantined','parsing','review_required','approved','published','rejected','rolled_back')",
            name="ck_knowledge_ingestion_lifecycle_status",
        ),
        CheckConstraint(
            "signature_status IN ('pending','match','mismatch','unsupported')",
            name="ck_knowledge_ingestion_signature_status",
        ),
        CheckConstraint(
            "parser_status IN ('not_started','running','passed','failed','timed_out','resource_exceeded')",
            name="ck_knowledge_ingestion_parser_status",
        ),
        CheckConstraint(
            "malware_status IN ('unavailable','pending','clean','malicious','error')",
            name="ck_knowledge_ingestion_malware_status",
        ),
        CheckConstraint(
            "cdr_status IN ('unavailable','pending','clean','sanitized','rejected','error')",
            name="ck_knowledge_ingestion_cdr_status",
        ),
        CheckConstraint(
            "prompt_risk_status IN ('pending','clear','review','blocked')",
            name="ck_knowledge_ingestion_prompt_risk_status",
        ),
        CheckConstraint(
            "source_trust IN ('untrusted','internal_unreviewed','internal_reviewed','external_verified')",
            name="ck_knowledge_ingestion_source_trust",
        ),
        CheckConstraint(
            "review_status IN ('pending','approved','rejected','re_review_required')",
            name="ck_knowledge_ingestion_review_status",
        ),
        CheckConstraint(
            "published_version IS NULL OR published_version > 0",
            name="ck_knowledge_ingestion_published_version_positive",
        ),
        Index(
            "ix_knowledge_ingestion_item_lifecycle",
            "knowledge_item_id",
            "lifecycle_status",
            "created_at",
        ),
        Index(
            "ix_knowledge_ingestion_publication",
            "knowledge_item_id",
            "published_version",
            "lifecycle_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    knowledge_item_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    sanitized_content_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    declared_mime_type: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    detected_mime_type: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    signature_status: Mapped[str] = mapped_column(String(40), default="pending", nullable=False, index=True)
    lifecycle_status: Mapped[str] = mapped_column(String(40), default="quarantined", nullable=False, index=True)
    parser_status: Mapped[str] = mapped_column(String(40), default="not_started", nullable=False, index=True)
    parser_identity: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    parser_version: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    malware_status: Mapped[str] = mapped_column(String(40), default="unavailable", nullable=False, index=True)
    cdr_status: Mapped[str] = mapped_column(String(40), default="unavailable", nullable=False, index=True)
    prompt_risk_status: Mapped[str] = mapped_column(String(40), default="pending", nullable=False, index=True)
    source_trust: Mapped[str] = mapped_column(String(40), default="untrusted", nullable=False, index=True)
    review_status: Mapped[str] = mapped_column(String(40), default="pending", nullable=False, index=True)
    safe_findings_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    rejection_reason: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    reviewed_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    published_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    rolled_back_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
        index=True,
    )


class KnowledgeIngestionAuditEvent(Base):
    """Append-only, bounded audit history for ingestion safety transitions."""

    __tablename__ = "knowledge_ingestion_audit_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('quarantined','parse_started','parse_passed','parse_failed','scanner_recorded','review_approved','rejected','published','rolled_back','re_review_requested')",
            name="ck_knowledge_ingestion_audit_event_type",
        ),
        Index(
            "ix_knowledge_ingestion_audit_sequence",
            "ingestion_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ingestion_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_ingestion_records.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    from_status: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    to_status: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    actor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    reason_code: Mapped[str] = mapped_column(String(120), nullable=False)
    safe_metadata_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False, index=True)
