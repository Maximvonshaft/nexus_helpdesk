from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .utils.time import utc_now

UTCDateTime = DateTime(timezone=True)


class IntegrationClientScope(Base):
    """Server-owned ownership boundary for one Integration principal.

    A Tenant principal may access exactly one relational Tenant. Platform scope is
    explicit and intentionally rare; the absence of a row is never interpreted as
    platform authority.
    """

    __tablename__ = "integration_client_scopes"
    __table_args__ = (
        CheckConstraint(
            "scope_type IN ('tenant','platform')",
            name="ck_integration_client_scope_type",
        ),
        CheckConstraint(
            "(scope_type = 'tenant' AND tenant_id IS NOT NULL) OR "
            "(scope_type = 'platform' AND tenant_id IS NULL)",
            name="ck_integration_client_scope_ownership",
        ),
        Index(
            "ix_integration_client_scopes_tenant_type",
            "tenant_id",
            "scope_type",
        ),
    )

    client_id: Mapped[int] = mapped_column(
        ForeignKey("integration_clients.id", ondelete="CASCADE"),
        primary_key=True,
    )
    scope_type: Mapped[str] = mapped_column(String(24), nullable=False)
    tenant_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    assigned_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    assignment_source: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
        default="explicit_admin_assignment",
    )
    assignment_version: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
        default="nexus.integration-principal-scope.v1",
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    client = relationship("IntegrationClient")
    tenant = relationship("Tenant")
    assigner = relationship("User")


__all__ = ["IntegrationClientScope"]
