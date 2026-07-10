from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .utils.time import utc_now


UTCDateTime = DateTime(timezone=True)
OPERATIONS_DISPATCH_STATUSES = (
    "pending",
    "processing",
    "dispatched",
    "retryable",
    "failed",
    "cancelled",
    "dead_letter",
)


class OperationsDispatchOutboxRecord(Base):
    """Durable state for an internal operations dispatch.

    Provider destinations are represented only by a business-safe key and a
    digest. Raw provider group identifiers and message bodies are intentionally
    excluded from this table.
    """

    __tablename__ = "operations_dispatch_outbox"
    __table_args__ = (
        UniqueConstraint("dispatch_key", name="uq_operations_dispatch_outbox_dispatch_key"),
        CheckConstraint(
            "status IN ('pending','processing','dispatched','retryable','failed','cancelled','dead_letter')",
            name="ck_operations_dispatch_outbox_status",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_operations_dispatch_outbox_attempt_count_nonnegative",
        ),
        CheckConstraint(
            "max_attempts >= 1",
            name="ck_operations_dispatch_outbox_max_attempts_positive",
        ),
        CheckConstraint(
            "((status = 'processing' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL) "
            "OR (status <> 'processing' AND lease_owner IS NULL AND lease_expires_at IS NULL))",
            name="ck_operations_dispatch_outbox_lease_state",
        ),
        CheckConstraint(
            "(status <> 'retryable' OR next_retry_at IS NOT NULL)",
            name="ck_operations_dispatch_outbox_retry_timestamp",
        ),
        CheckConstraint(
            "(status <> 'dispatched' OR dispatched_at IS NOT NULL)",
            name="ck_operations_dispatch_outbox_dispatched_timestamp",
        ),
        CheckConstraint(
            "(status <> 'cancelled' OR cancelled_at IS NOT NULL)",
            name="ck_operations_dispatch_outbox_cancelled_timestamp",
        ),
        Index(
            "ix_operations_dispatch_outbox_scope",
            "tenant_key",
            "country_code",
            "channel_key",
        ),
        Index(
            "ix_operations_dispatch_outbox_due",
            "status",
            "next_retry_at",
            "created_at",
        ),
        Index(
            "ix_operations_dispatch_outbox_lease",
            "status",
            "lease_expires_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tickets.id"), nullable=True, index=True)
    dispatch_key: Mapped[str] = mapped_column(String(80), nullable=False)
    tenant_key: Mapped[str] = mapped_column(String(80), nullable=False, default="default")
    country_code: Mapped[str] = mapped_column(String(16), nullable=False)
    channel_key: Mapped[str] = mapped_column(String(40), nullable=False)
    routing_rule_id: Mapped[int] = mapped_column(ForeignKey("whatsapp_routing_rules.id"), nullable=False, index=True)
    destination_group_key: Mapped[str] = mapped_column(String(200), nullable=False)
    destination_group_hash: Mapped[str] = mapped_column(String(80), nullable=False)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    lease_owner: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)

    provider_acknowledgement: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    external_reference_safe: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    error_category: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    error_summary_redacted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utc_now, onupdate=utc_now, index=True)
    dispatched_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
