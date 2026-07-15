from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .utils.time import utc_now

UTCDateTime = DateTime(timezone=True)


class WebchatPublicOriginBinding(Base):
    """Server-owned public WebChat routing identity."""

    __tablename__ = "webchat_public_origin_bindings"
    __table_args__ = (
        UniqueConstraint("normalized_origin", name="uq_webchat_public_origin_binding_origin"),
        Index(
            "ix_webchat_public_origin_binding_scope",
            "tenant_key",
            "country_code",
            "channel_key",
            "is_active",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    normalized_origin: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    tenant_key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    country_code: Mapped[Optional[str]] = mapped_column(String(8), nullable=True, index=True)
    channel_key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    updated_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utc_now, onupdate=utc_now)
