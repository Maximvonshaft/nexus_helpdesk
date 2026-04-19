from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .utils.time import utc_now

UTCDateTime = DateTime(timezone=True)


class TenantOpenClawAgent(Base):
    __tablename__ = 'tenant_openclaw_agents'
    __table_args__ = (UniqueConstraint('tenant_id', name='uq_tenant_openclaw_agent'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey('tenants.id'), index=True)
    openclaw_agent_id: Mapped[str] = mapped_column(String(160), index=True)
    agent_name: Mapped[str] = mapped_column(String(160))
    workspace_dir: Mapped[str] = mapped_column(String(500))
    deployment_mode: Mapped[str] = mapped_column(String(40), default='shared_gateway')
    binding_scope: Mapped[str] = mapped_column(String(120), default='tenant_default')
    binding_summary: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    identity_sync_status: Mapped[str] = mapped_column(String(40), default='pending')
    knowledge_sync_status: Mapped[str] = mapped_column(String(40), default='pending')
    identity_preview: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    bootstrap_preview: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_projected_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    last_projection_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now, index=True)
