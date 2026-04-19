from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .utils.time import utc_now

UTCDateTime = DateTime(timezone=True)


class OpenClawUnresolvedEvent(Base):
    __tablename__ = 'openclaw_unresolved_events'

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(120), index=True)
    session_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    channel: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    account_id: Mapped[Optional[str]] = mapped_column(String(160), nullable=True, index=True)
    thread_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    recipient: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    inferred_tenant_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    cursor_value: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    raw_event_json: Mapped[dict] = mapped_column(JSON)
    route_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    resolution_status: Mapped[str] = mapped_column(String(40), default='quarantined', index=True)
    resolution_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    replay_count: Mapped[int] = mapped_column(Integer, default=0)
    last_replayed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now, index=True)
