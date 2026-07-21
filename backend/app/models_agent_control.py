from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .utils.time import utc_now

UTCDateTime = DateTime(timezone=True)


class CustomerMemoryFact(Base):
    """Governed long-term customer fact used by the canonical Agent context.

    The table stores only bounded, explicitly sourced facts. Secrets and raw
    conversation transcripts are prohibited by the service boundary.
    """

    __tablename__ = "customer_memory_facts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_key",
            "customer_id",
            "memory_key",
            name="uq_customer_memory_fact_scope_key",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_customer_memory_confidence_range",
        ),
        CheckConstraint(
            "sensitivity IN ('standard', 'restricted')",
            name="ck_customer_memory_sensitivity",
        ),
        Index(
            "ix_customer_memory_runtime_lookup",
            "tenant_key",
            "customer_id",
            "is_active",
            "expires_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_key: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    memory_key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    value_text: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False, default="operator")
    source_reference: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    consent_basis: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    sensitivity: Mapped[str] = mapped_column(String(20), nullable=False, default="standard")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    last_confirmed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    updated_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, onupdate=utc_now, nullable=False
    )

    customer = relationship("Customer")
