from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .utils.time import utc_now

UTCDateTime = DateTime(timezone=True)


class KnowledgeIngestionRecord(Base):
    """Server-owned safety state for one uploaded Knowledge artifact.

    The record stores bounded metadata only. Raw file bytes, extracted text,
    customer data, scanner payloads and credentials must never be persisted here.
    Publication eligibility is derived by policy and is intentionally not stored
    as a caller-controlled boolean.
    """

    __tablename__ = "knowledge_ingestion_records"
    __table_args__ = (
        UniqueConstraint(
            "knowledge_item_id",
            "content_sha256",
            name="uq_knowledge_ingestion_item_content",
        ),
        UniqueConstraint(
            "tenant_key",
            "storage_key",
            name="uq_knowledge_ingestion_tenant_storage",
        ),
        CheckConstraint("size_bytes >= 0", name="ck_knowledge_ingestion_size_nonnegative"),
        CheckConstraint(
            "length(content_sha256) = 64",
            name="ck_knowledge_ingestion_sha256_length",
        ),
        CheckConstraint(
            "lifecycle_status IN ('quarantined','scanning','review_required','approved','rejected','superseded')",
            name="ck_knowledge_ingestion_lifecycle_status",
        ),
        CheckConstraint(
            "signature_status IN ('pending','match','mismatch','unsupported')",
            name="ck_knowledge_ingestion_signature_status",
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
            "review_status IN ('pending','approved','rejected')",
            name="ck_knowledge_ingestion_review_status",
        ),
        Index(
            "ix_knowledge_ingestion_scope_status",
            "tenant_key",
            "lifecycle_status",
            "created_at",
        ),
        Index(
            "ix_knowledge_ingestion_item_status",
            "knowledge_item_id",
            "lifecycle_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    knowledge_item_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_key: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    storage_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    declared_mime_type: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    detected_media_type: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    signature_status: Mapped[str] = mapped_column(String(40), default="pending", nullable=False, index=True)
    lifecycle_status: Mapped[str] = mapped_column(String(40), default="quarantined", nullable=False, index=True)
    malware_status: Mapped[str] = mapped_column(String(40), default="unavailable", nullable=False, index=True)
    cdr_status: Mapped[str] = mapped_column(String(40), default="unavailable", nullable=False, index=True)
    prompt_risk_status: Mapped[str] = mapped_column(String(40), default="pending", nullable=False, index=True)
    source_trust: Mapped[str] = mapped_column(String(40), default="untrusted", nullable=False, index=True)
    review_status: Mapped[str] = mapped_column(String(40), default="pending", nullable=False, index=True)
    parser_name: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    parser_version: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    safe_findings_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    rejection_reason: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    reviewed_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    scanned_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
        index=True,
    )
