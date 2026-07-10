from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .utils.time import utc_now

UTCDateTime = DateTime(timezone=True)


class CaseContextRecord(Base):
    __tablename__ = "case_contexts"
    __table_args__ = (
        Index(
            "uq_case_context_active_conversation_only",
            "tenant_id",
            "conversation_id",
            unique=True,
            sqlite_where=text("is_active = 1 AND conversation_id IS NOT NULL AND ticket_id IS NULL"),
            postgresql_where=text("is_active IS TRUE AND conversation_id IS NOT NULL AND ticket_id IS NULL"),
        ),
        Index(
            "uq_case_context_active_ticket_only",
            "tenant_id",
            "ticket_id",
            unique=True,
            sqlite_where=text("is_active = 1 AND conversation_id IS NULL AND ticket_id IS NOT NULL"),
            postgresql_where=text("is_active IS TRUE AND conversation_id IS NULL AND ticket_id IS NOT NULL"),
        ),
        Index(
            "uq_case_context_active_conversation_ticket",
            "tenant_id",
            "conversation_id",
            "ticket_id",
            unique=True,
            sqlite_where=text("is_active = 1 AND conversation_id IS NOT NULL AND ticket_id IS NOT NULL"),
            postgresql_where=text("is_active IS TRUE AND conversation_id IS NOT NULL AND ticket_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), default="default", index=True)
    conversation_id: Mapped[Optional[int]] = mapped_column(ForeignKey("webchat_conversations.id"), nullable=True, index=True)
    ticket_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tickets.id"), nullable=True, index=True)
    channel: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    country_code: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True)
    issue_type: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(40), default="active", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    safe_tracking_reference: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    tracking_number_hash: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    contact_methods_json: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    customer_claim_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_mcp_fact_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    missing_info_json: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    handoff_requested: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    ticket_created: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    routed_group_key: Mapped[Optional[str]] = mapped_column(String(160), nullable=True, index=True)
    ai_actions_taken_json: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    agent_handover_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now, index=True)


class HumanHoursPolicyRecord(Base):
    __tablename__ = "human_hours_policies"
    __table_args__ = (
        UniqueConstraint("country_code", "channel", "queue_key", name="uq_human_hours_country_channel_queue"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    country_code: Mapped[str] = mapped_column(String(16), default="GLOBAL", index=True)
    channel: Mapped[str] = mapped_column(String(40), default="all", index=True)
    queue_key: Mapped[str] = mapped_column(String(120), index=True)
    timezone_name: Mapped[str] = mapped_column(String(80), default="UTC")
    working_hours_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    holiday_calendar_json: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    handoff_enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    offline_message_template: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    auto_ticket_when_offline: Mapped[bool] = mapped_column(Boolean, default=True)
    customer_wait_timeout_seconds: Mapped[int] = mapped_column(Integer, default=180)
    fallback_action: Mapped[str] = mapped_column(String(80), default="create_ticket")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now, index=True)


class EscalationPolicyRecord(Base):
    __tablename__ = "escalation_policies"
    __table_args__ = (
        UniqueConstraint("risk_key", "country_code", "channel", name="uq_escalation_policy_scope"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    risk_key: Mapped[str] = mapped_column(String(120), index=True)
    country_code: Mapped[str] = mapped_column(String(16), default="GLOBAL", index=True)
    channel: Mapped[str] = mapped_column(String(40), default="all", index=True)
    trigger_patterns_json: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    semantic_intents_json: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    max_ai_attempts: Mapped[int] = mapped_column(Integer, default=2)
    action: Mapped[str] = mapped_column(String(80), default="handoff_or_ticket")
    handoff_required: Mapped[bool] = mapped_column(Boolean, default=True)
    ticket_required: Mapped[bool] = mapped_column(Boolean, default=True)
    forbidden_commitments_json: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    allowed_resolution_actions_json: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now, index=True)


class ToolExecutionPolicyRecord(Base):
    __tablename__ = "tool_execution_policies"
    __table_args__ = (
        UniqueConstraint("tool_name", "country_code", "channel", name="uq_tool_execution_policy_scope"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tool_name: Mapped[str] = mapped_column(String(160), index=True)
    country_code: Mapped[str] = mapped_column(String(16), default="GLOBAL", index=True)
    channel: Mapped[str] = mapped_column(String(40), default="all", index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    ai_auto_executable: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    risk_level: Mapped[str] = mapped_column(String(40), default="low", index=True)
    requires_tracking_number: Mapped[bool] = mapped_column(Boolean, default=False)
    requires_contact: Mapped[bool] = mapped_column(Boolean, default=False)
    requires_customer_confirmation: Mapped[bool] = mapped_column(Boolean, default=False)
    requires_human_confirmation: Mapped[bool] = mapped_column(Boolean, default=False)
    allowed_channels_json: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    allowed_countries_json: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    customer_visible_success_template: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    customer_visible_failure_template: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    audit_level: Mapped[str] = mapped_column(String(80), default="standard")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now, index=True)


class WhatsAppRoutingRuleRecord(Base):
    __tablename__ = "whatsapp_routing_rules"
    __table_args__ = (
        UniqueConstraint("country_code", "issue_type", "channel", name="uq_whatsapp_routing_scope"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    country_code: Mapped[str] = mapped_column(String(16), index=True)
    issue_type: Mapped[str] = mapped_column(String(120), index=True)
    channel: Mapped[str] = mapped_column(String(40), default="whatsapp", index=True)
    destination_group_id: Mapped[str] = mapped_column(String(200))
    fallback_group_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    working_hours_key: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    message_template: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=100, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now, index=True)


class RuntimeDecisionAuditRecord(Base):
    __tablename__ = "runtime_decision_audits"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(80), default="default", index=True)
    channel: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    country_code: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True)
    conversation_id: Mapped[Optional[int]] = mapped_column(ForeignKey("webchat_conversations.id"), nullable=True, index=True)
    ticket_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tickets.id"), nullable=True, index=True)
    business_reply_type: Mapped[str] = mapped_column(String(120), index=True)
    next_action: Mapped[str] = mapped_column(String(120), index=True)
    risk_level: Mapped[str] = mapped_column(String(40), default="low", index=True)
    allowed: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    violations_json: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    warnings_json: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    decision_json: Mapped[dict] = mapped_column(JSON)
    case_context_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
