from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .utils.time import utc_now

UTCDateTime = DateTime(timezone=True)


class BackgroundJobScope(Base):
    """Canonical execution envelope for one durable BackgroundJob.

    BackgroundJob remains the queue/lease record. This one-to-one row carries the
    server-derived Tenant, data-subject purpose and source identity required for
    authorization, privacy enforcement and safe operational recovery. It is not a
    second queue or business state machine.
    """

    __tablename__ = "background_job_scopes"
    __table_args__ = (
        CheckConstraint(
            "scope_type IN ('tenant','platform','unresolved')",
            name="ck_background_job_scope_type",
        ),
        CheckConstraint(
            "scope_type <> 'tenant' OR tenant_id IS NOT NULL",
            name="ck_background_job_scope_tenant_required",
        ),
        CheckConstraint(
            "scope_type = 'tenant' OR tenant_id IS NULL",
            name="ck_background_job_scope_non_tenant_has_no_tenant",
        ),
        CheckConstraint(
            "length(trim(purpose)) > 0",
            name="ck_background_job_scope_purpose_nonempty",
        ),
        Index(
            "ix_background_job_scope_tenant_purpose",
            "tenant_id",
            "purpose",
            "job_id",
        ),
        Index(
            "ix_background_job_scope_customer_purpose",
            "customer_id",
            "purpose",
            "job_id",
        ),
        Index(
            "ix_background_job_scope_resource",
            "resource_type",
            "resource_id",
        ),
    )

    job_id: Mapped[int] = mapped_column(
        ForeignKey("background_jobs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    scope_type: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="unresolved",
        index=True,
    )
    tenant_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    customer_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    purpose: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_type: Mapped[Optional[str]] = mapped_column(
        String(80),
        nullable=True,
    )
    resource_id: Mapped[Optional[str]] = mapped_column(
        String(180),
        nullable=True,
    )
    source_schema: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
        default="nexus.background-job-scope.v1",
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        default=utc_now,
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
