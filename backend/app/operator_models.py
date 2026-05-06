from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .utils.time import utc_now

UTCDateTime = DateTime(timezone=True)


class OperatorTask(Base):
    """Minimal operator queue task for handoff / unresolved workflows.

    This table is intentionally small. It projects AI fallback, WebChat handoff,
    and OpenClaw unresolved work into one operational queue without introducing a
    workflow engine.
    """

    __tablename__ = "operator_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_type: Mapped[str] = mapped_column(String(40), index=True)
    source_id: Mapped[Optional[str]] = mapped_column(String(160), nullable=True, index=True)
    ticket_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    webchat_conversation_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    unresolved_event_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    task_type: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=100, index=True)
    assignee_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    reason_code: Mapped[Optional[str]] = mapped_column(String(160), nullable=True, index=True)
    payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now, index=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
