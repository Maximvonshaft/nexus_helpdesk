from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .utils.time import utc_now

UTCDateTime = DateTime(timezone=True)


class WebchatVoiceSession(Base):
    """Durable business state for one WebChat internet voice call."""

    __tablename__ = "webchat_voice_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("webchat_conversations.id"), index=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    provider: Mapped[str] = mapped_column(String(40), default="mock", index=True)
    provider_room_name: Mapped[str] = mapped_column(String(160), index=True)
    status: Mapped[str] = mapped_column(String(40), default="created", index=True)
    mode: Mapped[str] = mapped_column(String(40), default="visitor_to_agent")
    locale: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    recording_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    recording_status: Mapped[str] = mapped_column(String(40), default="disabled", index=True)
    transcript_status: Mapped[str] = mapped_column(String(40), default="disabled", index=True)
    summary_status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    accepted_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    ended_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    ringing_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    accepted_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    active_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now, index=True)


class WebchatVoiceParticipant(Base):
    """Participant record for visitor, agent, AI, or future transcriber worker."""

    __tablename__ = "webchat_voice_participants"
    __table_args__ = (
        UniqueConstraint("voice_session_id", "provider_identity", name="uq_voice_participant_session_identity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    voice_session_id: Mapped[int] = mapped_column(ForeignKey("webchat_voice_sessions.id"), index=True)
    participant_type: Mapped[str] = mapped_column(String(40), index=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    visitor_label: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    provider_identity: Mapped[str] = mapped_column(String(160), index=True)
    status: Mapped[str] = mapped_column(String(40), default="invited", index=True)
    joined_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    left_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)


class WebchatVoiceTranscriptSegment(Base):
    """Final transcript segment storage. Write logic is added in later transcription PRs."""

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
    voice_session_id: Mapped[int] = mapped_column(ForeignKey("webchat_voice_sessions.id"), index=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("webchat_conversations.id"), index=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    provider_session_id: Mapped[Optional[str]] = mapped_column(String(160), nullable=True, index=True)
    provider_item_id: Mapped[Optional[str]] = mapped_column(String(160), nullable=True, index=True)
    participant_identity: Mapped[str] = mapped_column(String(160), index=True)
    speaker_type: Mapped[str] = mapped_column(String(40), index=True)
    speaker_label: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    segment_id: Mapped[str] = mapped_column(String(160), index=True)
    language: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    is_final: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    start_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    end_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    text_raw: Mapped[str] = mapped_column(Text)
    text_redacted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    redaction_status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
