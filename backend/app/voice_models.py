from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from .db import Base
from .utils.time import ensure_utc, utc_now


class AwareUTCDateTime(TypeDecorator):
    """Return timezone-aware UTC values even when SQLite drops tzinfo."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        normalized = ensure_utc(value)
        if normalized is None:
            return None
        if dialect.name == "sqlite":
            return normalized.replace(tzinfo=None)
        return normalized

    def process_result_value(self, value, dialect):
        del dialect
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


UTCDateTime = AwareUTCDateTime


class WebchatVoiceSession(Base):
    """Canonical voice projection for one Conversation in the LiveKit media plane.

    Human ownership is intentionally absent. The accepted Handoff assignment and
    ``WebchatConversation.active_agent_id`` are the only human ownership authority.
    """

    __tablename__ = "webchat_voice_sessions"
    __table_args__ = (
        Index("ix_voice_sessions_provider_room", "provider", "provider_room_name"),
        Index("ix_voice_sessions_status_created", "status", "created_at"),
        CheckConstraint(
            "direction IN ('inbound', 'outbound')",
            name="ck_webchat_voice_session_direction",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("webchat_conversations.id"), index=True
    )
    ticket_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("tickets.id"), nullable=True, index=True
    )
    channel_account_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("channel_accounts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    handoff_request_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("webchat_handoff_requests.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(40), default="mock", index=True)
    provider_room_name: Mapped[str] = mapped_column(String(160), index=True)
    provider_call_id: Mapped[Optional[str]] = mapped_column(
        String(160), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(40), default="created", index=True)
    mode: Mapped[str] = mapped_column(String(40), default="visitor_to_agent")
    direction: Mapped[str] = mapped_column(String(16), default="inbound")
    locale: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    caller_number_hash: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, index=True
    )
    called_number: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True, index=True
    )
    recording_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    recording_status: Mapped[str] = mapped_column(
        String(40), default="disabled", index=True
    )
    recording_provider_ref: Mapped[Optional[str]] = mapped_column(
        String(180), nullable=True, index=True
    )
    transcript_status: Mapped[str] = mapped_column(
        String(40), default="disabled", index=True
    )
    summary_status: Mapped[str] = mapped_column(
        String(40), default="pending", index=True
    )
    ai_agent_status: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True, index=True
    )
    ai_agent_started_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True
    )
    ai_agent_ended_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True
    )
    ai_handoff_reason: Mapped[Optional[str]] = mapped_column(
        String(240), nullable=True
    )
    ai_language: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True, index=True
    )
    ai_turn_count: Mapped[int] = mapped_column(Integer, default=0)
    ai_agent_worker_id: Mapped[Optional[str]] = mapped_column(
        String(120), nullable=True, index=True
    )
    ai_agent_claimed_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True
    )
    ai_agent_lease_expires_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True, index=True
    )
    ai_agent_last_heartbeat_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True
    )
    ai_agent_error_code: Mapped[Optional[str]] = mapped_column(
        String(120), nullable=True, index=True
    )
    ai_agent_error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ended_by_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    ringing_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True, index=True
    )
    accepted_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True, index=True
    )
    active_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True, index=True
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True, index=True
    )
    wrap_up_expires_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True, index=True
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, onupdate=utc_now, index=True
    )


class WebchatVoiceParticipant(Base):
    """Provider Call Leg projection for caller, AI, human, transfer and service legs."""

    __tablename__ = "webchat_voice_participants"
    __table_args__ = (
        UniqueConstraint(
            "voice_session_id",
            "provider_identity",
            name="uq_voice_participant_session_identity",
        ),
        Index("ix_voice_call_leg_session_type", "voice_session_id", "participant_type"),
        CheckConstraint(
            "direction IN ('inbound', 'outbound', 'internal')",
            name="ck_voice_call_leg_direction",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    voice_session_id: Mapped[int] = mapped_column(
        ForeignKey("webchat_voice_sessions.id", ondelete="CASCADE"), index=True
    )
    parent_leg_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("webchat_voice_participants.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    participant_type: Mapped[str] = mapped_column(String(40), index=True)
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    visitor_label: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    provider_identity: Mapped[str] = mapped_column(String(160), index=True)
    provider_call_id: Mapped[Optional[str]] = mapped_column(
        String(160), nullable=True, index=True
    )
    direction: Mapped[str] = mapped_column(
        String(16), default="internal", index=True
    )
    status: Mapped[str] = mapped_column(String(40), default="invited", index=True)
    termination_reason: Mapped[Optional[str]] = mapped_column(
        String(160), nullable=True, index=True
    )
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True, index=True
    )
    answered_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True, index=True
    )
    joined_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True, index=True
    )
    left_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True, index=True
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, onupdate=utc_now, index=True
    )


class VoiceRoutingOffer(Base):
    """Short-lived agent ringing offer; never human ownership."""

    __tablename__ = "voice_routing_offers"
    __table_args__ = (
        UniqueConstraint(
            "voice_session_id",
            "agent_id",
            "sequence",
            name="uq_voice_offer_session_agent_sequence",
        ),
        CheckConstraint(
            "status IN ('offered', 'accepted', 'declined', 'expired', 'cancelled')",
            name="ck_voice_routing_offer_status",
        ),
        Index(
            "ix_voice_offer_agent_status_expiry",
            "agent_id",
            "status",
            "expires_at",
        ),
        Index("ix_voice_offer_session_status", "voice_session_id", "status"),
        Index(
            "uq_voice_offer_active_session",
            "voice_session_id",
            unique=True,
            sqlite_where=text("status = 'offered'"),
            postgresql_where=text("status = 'offered'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    voice_session_id: Mapped[int] = mapped_column(
        ForeignKey("webchat_voice_sessions.id", ondelete="CASCADE"), index=True
    )
    handoff_request_id: Mapped[int] = mapped_column(
        ForeignKey("webchat_handoff_requests.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="offered", index=True)
    offered_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    accepted_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    declined_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    expired_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    decline_reason: Mapped[Optional[str]] = mapped_column(String(240), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, onupdate=utc_now, index=True
    )


class WebchatVoiceTranscriptSegment(Base):
    """Final transcript segment storage for the canonical LiveKit session."""

    __tablename__ = "webchat_voice_transcript_segments"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "provider_session_id",
            "segment_id",
            "participant_identity",
            name="uq_voice_transcript_provider_session_segment_participant",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    voice_session_id: Mapped[int] = mapped_column(
        ForeignKey("webchat_voice_sessions.id"), index=True
    )
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("webchat_conversations.id"), index=True
    )
    ticket_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("tickets.id"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(40), index=True)
    provider_session_id: Mapped[Optional[str]] = mapped_column(
        String(160), nullable=True, index=True
    )
    provider_item_id: Mapped[Optional[str]] = mapped_column(
        String(160), nullable=True, index=True
    )
    participant_identity: Mapped[str] = mapped_column(String(160), index=True)
    speaker_type: Mapped[str] = mapped_column(String(40), index=True)
    speaker_label: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    segment_id: Mapped[str] = mapped_column(String(160), index=True)
    language: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True, index=True
    )
    is_final: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    start_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    end_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    text_raw: Mapped[str] = mapped_column(Text)
    text_redacted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    redaction_status: Mapped[str] = mapped_column(
        String(40), default="pending", index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, index=True
    )


class WebchatVoiceAITurn(Base):
    """Redacted AI turn record for the unified Agent Runtime voice path."""

    __tablename__ = "webchat_voice_ai_turns"
    __table_args__ = (
        UniqueConstraint(
            "voice_session_id", "turn_index", name="uq_voice_ai_turn_session_index"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    voice_session_id: Mapped[int] = mapped_column(
        ForeignKey("webchat_voice_sessions.id"), index=True
    )
    conversation_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("webchat_conversations.id"), nullable=True, index=True
    )
    ticket_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("tickets.id"), nullable=True, index=True
    )
    turn_index: Mapped[int] = mapped_column(Integer, index=True)
    customer_text_redacted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ai_response_text_redacted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    language: Mapped[Optional[str]] = mapped_column(
        String(20), nullable=True, index=True
    )
    intent: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    action: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    tracking_number_hash: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True, index=True
    )
    handoff_required: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    handoff_reason: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    confidence: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    provider: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    stt_provider: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    tts_provider: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, index=True
    )


class WebchatVoiceAIAction(Base):
    """Agent Runtime decision record for AI-requested business actions."""

    __tablename__ = "webchat_voice_ai_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    voice_session_id: Mapped[int] = mapped_column(
        ForeignKey("webchat_voice_sessions.id"), index=True
    )
    turn_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("webchat_voice_ai_turns.id"), nullable=True, index=True
    )
    model_action: Mapped[str] = mapped_column(String(80), index=True)
    nexus_decision: Mapped[str] = mapped_column(String(40), index=True)
    decision_reason: Mapped[Optional[str]] = mapped_column(String(240), nullable=True)
    speedaf_tool_name: Mapped[Optional[str]] = mapped_column(
        String(160), nullable=True, index=True
    )
    background_job_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("background_jobs.id"), nullable=True, index=True
    )
    tool_call_log_id: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, index=True
    )
    result_status: Mapped[Optional[str]] = mapped_column(
        String(80), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, index=True
    )


class WebchatVoiceSessionAction(Base):
    """Durable Voice Command outbox and provider-result evidence."""

    __tablename__ = "webchat_voice_session_actions"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key", name="uq_voice_session_action_idempotency_key"
        ),
        CheckConstraint(
            "status IN ('requested', 'dispatching', 'succeeded', 'failed', 'retryable', 'cancelled')",
            name="ck_voice_command_status",
        ),
        Index("ix_voice_session_actions_status_created", "status", "created_at"),
        Index(
            "ix_voice_command_dispatch",
            "status",
            "next_attempt_at",
            "lease_expires_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    voice_session_id: Mapped[int] = mapped_column(
        ForeignKey("webchat_voice_sessions.id"), index=True
    )
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("webchat_conversations.id"), index=True
    )
    ticket_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("tickets.id"), nullable=True, index=True
    )
    actor_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    action_type: Mapped[str] = mapped_column(String(40), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="requested", index=True)
    provider_status: Mapped[str] = mapped_column(
        String(40), default="pending", index=True
    )
    provider_reason: Mapped[Optional[str]] = mapped_column(
        String(160), nullable=True, index=True
    )
    provider_reference: Mapped[Optional[str]] = mapped_column(
        String(180), nullable=True, index=True
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_attempt_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True
    )
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True, index=True
    )
    lease_owner: Mapped[Optional[str]] = mapped_column(
        String(120), nullable=True, index=True
    )
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True, index=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True
    )
    payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    result_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ticket_event_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ticket_events.id"), nullable=True, index=True
    )
    webchat_event_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("webchat_events.id"), nullable=True, index=True
    )
    audit_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("admin_audit_logs.id"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, onupdate=utc_now, index=True
    )


class VoiceChannelConfiguration(Base):
    """One-to-one LiveKit/SIP policy projection for the canonical ChannelAccount."""

    __tablename__ = "voice_channel_configurations"
    __table_args__ = (
        UniqueConstraint(
            "channel_account_id", name="uq_voice_channel_configuration_account"
        ),
        CheckConstraint(
            "routing_mode IN ('ai_first', 'human_first')",
            name="ck_voice_channel_configuration_routing_mode",
        ),
        CheckConstraint(
            "queue_timeout_seconds BETWEEN 15 AND 3600",
            name="ck_voice_channel_configuration_queue_timeout",
        ),
        CheckConstraint(
            "offer_timeout_seconds BETWEEN 5 AND 120",
            name="ck_voice_channel_configuration_offer_timeout",
        ),
        CheckConstraint(
            "wrap_up_seconds BETWEEN 0 AND 900",
            name="ck_voice_channel_configuration_wrap_up",
        ),
        CheckConstraint(
            "recording_policy IN ('disabled', 'consent_required', 'always')",
            name="ck_voice_channel_configuration_recording_policy",
        ),
        CheckConstraint(
            "transcription_policy IN ('disabled', 'consent_required', 'always')",
            name="ck_voice_channel_configuration_transcription_policy",
        ),
        CheckConstraint(
            "overflow_action IN ('ai', 'voicemail', 'disconnect')",
            name="ck_voice_channel_configuration_overflow_action",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_account_id: Mapped[int] = mapped_column(
        ForeignKey("channel_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    livekit_project_ref: Mapped[Optional[str]] = mapped_column(
        String(160), nullable=True
    )
    inbound_trunk_id: Mapped[Optional[str]] = mapped_column(
        String(160), nullable=True, index=True
    )
    outbound_trunk_id: Mapped[Optional[str]] = mapped_column(
        String(160), nullable=True
    )
    dispatch_rule_id: Mapped[Optional[str]] = mapped_column(
        String(160), nullable=True, index=True
    )
    routing_mode: Mapped[str] = mapped_column(
        String(24), nullable=False, default="ai_first"
    )
    ai_agent_name: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    business_hours_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    queue_timeout_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=90
    )
    offer_timeout_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=20
    )
    wrap_up_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30
    )
    overflow_action: Mapped[str] = mapped_column(
        String(24), nullable=False, default="ai"
    )
    voicemail_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    recording_policy: Mapped[str] = mapped_column(
        String(32), nullable=False, default="disabled"
    )
    transcription_policy: Mapped[str] = mapped_column(
        String(32), nullable=False, default="disabled"
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, onupdate=utc_now, nullable=False
    )


class TelephonyEventInbox(Base):
    """Idempotent, tenant-resolved Provider Event Inbox with retry/dead-letter state."""

    __tablename__ = "telephony_event_inbox"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "provider_event_id",
            name="uq_telephony_event_provider_identity",
        ),
        CheckConstraint(
            "status IN ('received', 'processing', 'processed', 'ignored', 'retryable', 'failed', 'dead_letter')",
            name="ck_telephony_event_inbox_status",
        ),
        Index(
            "ix_telephony_event_inbox_status_received", "status", "received_at"
        ),
        Index("ix_telephony_event_retry", "status", "next_attempt_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(180), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    safe_payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    raw_payload_object_key: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="received"
    )
    tenant_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True, index=True
    )
    channel_account_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("channel_accounts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    voice_session_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("webchat_voice_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_code: Mapped[Optional[str]] = mapped_column(
        String(120), nullable=True
    )
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True, index=True
    )
    processing_started_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True
    )
    received_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, nullable=False
    )
    processed_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True
    )
    dead_lettered_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True
    )
