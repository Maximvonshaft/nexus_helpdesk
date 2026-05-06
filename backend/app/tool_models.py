from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .utils.time import utc_now

UTCDateTime = DateTime(timezone=True)


class ToolRegistry(Base):
    """Audit-only registry row for Bridge/MCP tools.

    The registry deliberately starts as metadata and audit support. It does not
    enforce allow/deny decisions yet so existing production integration paths do
    not change behavior in this P0 closure PR.
    """

    __tablename__ = "tool_registry"

    id: Mapped[int] = mapped_column(primary_key=True)
    tool_name: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(80), default="openclaw", index=True)
    tool_type: Mapped[str] = mapped_column(String(40), default="read_only", index=True)
    capability_scope: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    default_timeout_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_timeout_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    retry_policy: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    risk_level: Mapped[str] = mapped_column(String(40), default="low", index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    audit_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class ToolCallLog(Base):
    """Safe audit record for one tool invocation.

    Input/output fields store bounded summaries and hashes only. Raw prompts,
    tokens, secrets, full message bodies, and full PII payloads must not be
    stored here.
    """

    __tablename__ = "tool_call_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    tool_name: Mapped[str] = mapped_column(String(160), index=True)
    provider: Mapped[str] = mapped_column(String(80), default="openclaw", index=True)
    tool_type: Mapped[str] = mapped_column(String(40), default="read_only", index=True)
    conversation_id: Mapped[Optional[str]] = mapped_column(String(160), nullable=True, index=True)
    webchat_conversation_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    ticket_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    ai_turn_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    background_job_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    actor_type: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    actor_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    request_id: Mapped[Optional[str]] = mapped_column(String(160), nullable=True, index=True)
    input_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    input_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    output_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="success", index=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    elapsed_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    timeout_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    redaction_applied: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)


class ToolCapability(Base):
    __tablename__ = "tool_capabilities"
    __table_args__ = (UniqueConstraint("capability", "tool_name", name="uq_tool_capability_tool"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    capability: Mapped[str] = mapped_column(String(160), index=True)
    tool_name: Mapped[str] = mapped_column(String(160), index=True)
    allowed: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)
