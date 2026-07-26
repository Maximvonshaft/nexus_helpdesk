from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .utils.time import utc_now

UTCDateTime = DateTime(timezone=True)


class DataProcessingRestriction(Base):
    """Executable Tenant-scoped restriction on non-essential processing."""

    __tablename__ = "data_processing_restrictions"
    __table_args__ = (
        UniqueConstraint(
            "request_id",
            name="uq_data_processing_restriction_request",
        ),
        CheckConstraint(
            "status IN ('active','released')",
            name="ck_data_processing_restriction_status",
        ),
        Index(
            "ix_data_processing_restriction_customer_status",
            "tenant_id",
            "customer_id",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    request_id: Mapped[int] = mapped_column(
        ForeignKey("data_subject_requests.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="active",
        index=True,
    )
    blocked_purposes_json: Mapped[list] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    allowed_purposes_json: Mapped[list] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    reason_code: Mapped[str] = mapped_column(String(120), nullable=False)
    placed_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    released_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    placed_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        default=utc_now,
        index=True,
    )
    released_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime,
        nullable=True,
        index=True,
    )
