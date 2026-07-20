from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .utils.time import utc_now

UTCDateTime = DateTime(timezone=True)


class WebchatAIDebugRun(Base):
    __tablename__ = "webchat_ai_debug_runs"
    __table_args__ = (UniqueConstraint("ai_turn_id", name="uq_webchat_ai_debug_runs_ai_turn_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("webchat_conversations.id"), index=True)
    ticket_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tickets.id"), nullable=True, index=True)
    ai_turn_id: Mapped[int] = mapped_column(ForeignKey("webchat_ai_turns.id"), index=True)
    visitor_message_id: Mapped[Optional[int]] = mapped_column(ForeignKey("webchat_messages.id"), nullable=True, index=True)
    reply_message_id: Mapped[Optional[int]] = mapped_column(ForeignKey("webchat_messages.id"), nullable=True, index=True)
    request_id: Mapped[Optional[str]] = mapped_column(String(160), nullable=True, index=True)
    channel: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(40), default="unknown", index=True)
    status_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    intent: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    reply_type: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    reply_source: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    provider_status: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    tracking_intent_detected: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    tracking_fact_evidence_present: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    tool_facts_present: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    live_tracking_answer_allowed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    kb_hits_count: Mapped[int] = mapped_column(Integer, default=0, index=True)
    tool_call_count: Mapped[int] = mapped_column(Integer, default=0)
    runtime_event_count: Mapped[int] = mapped_column(Integer, default=0)
    prior_ai_messages_count: Mapped[int] = mapped_column(Integer, default=0)
    customer_claim_count: Mapped[int] = mapped_column(Integer, default=0)
    memory_system: Mapped[str] = mapped_column(String(80), default="unknown", index=True)
    support_memory_ledger_used_by_runtime: Mapped[bool] = mapped_column(Boolean, default=False)
    safety_status: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    fact_gate_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    customer_visible_message_created: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    debug_bundle_json: Mapped[str] = mapped_column(Text)
    privacy_report_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now, index=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)


class WebchatAITestFinding(Base):
    __tablename__ = "webchat_ai_test_findings"

    id: Mapped[int] = mapped_column(primary_key=True)
    debug_run_id: Mapped[int] = mapped_column(ForeignKey("webchat_ai_debug_runs.id"), index=True)
    ai_turn_id: Mapped[int] = mapped_column(ForeignKey("webchat_ai_turns.id"), index=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("webchat_conversations.id"), index=True)
    ticket_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tickets.id"), nullable=True, index=True)
    finding_type: Mapped[str] = mapped_column(String(120), index=True)
    severity: Mapped[str] = mapped_column(String(40), default="medium", index=True)
    tester_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    expected_behavior: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    actual_behavior: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    bundle_snapshot_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="open", index=True)
    linked_issue_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now, index=True)


class WebchatAIEvalCase(Base):
    __tablename__ = "webchat_ai_eval_cases"
    __table_args__ = (UniqueConstraint("case_key", name="uq_webchat_ai_eval_cases_case_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    case_key: Mapped[str] = mapped_column(String(200), index=True)
    source_debug_run_id: Mapped[int] = mapped_column(ForeignKey("webchat_ai_debug_runs.id"), index=True)
    source_finding_id: Mapped[Optional[int]] = mapped_column(ForeignKey("webchat_ai_test_findings.id"), nullable=True, index=True)
    scenario: Mapped[str] = mapped_column(Text)
    intent: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    channel: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    language: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True)
    input_redacted_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    expected_policy_json: Mapped[str] = mapped_column(Text)
    expected_reply_type: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    required_evidence_json: Mapped[str] = mapped_column(Text)
    forbidden_sources_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="active", index=True)
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now, index=True)
