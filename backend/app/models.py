from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .enums import (
    ConversationState,
    EventType,
    JobStatus,
    MessageStatus,
    NoteVisibility,
    ResolutionCategory,
    SourceChannel,
    TicketPriority,
    TicketSource,
    TicketStatus,
    UserRole,
)
from .utils.time import utc_now

UTCDateTime = DateTime(timezone=True)


def _canonical_payload_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _payload_hash_from_json_text(payload_json: str | None) -> str:
    try:
        payload = json.loads(payload_json or "{}")
    except Exception:
        payload = payload_json or ""
    return hashlib.sha256(_canonical_payload_json(payload).encode("utf-8")).hexdigest()


def _openclaw_payload_hash_default(context) -> str:
    return _payload_hash_from_json_text(context.get_current_parameters().get("payload_json"))


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    team_type: Mapped[str] = mapped_column(String(80), default="support")
    market_id: Mapped[Optional[int]] = mapped_column(ForeignKey("markets.id"), nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)

    users: Mapped[list["User"]] = relationship(back_populates="team")
    market: Mapped[Optional["Market"]] = relationship(back_populates="teams")




class Market(Base):
    __tablename__ = "markets"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    country_code: Mapped[str] = mapped_column(String(8), index=True)
    language_code: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    timezone: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)

    teams: Mapped[list["Team"]] = relationship(back_populates="market")
    channel_accounts: Mapped[list["ChannelAccount"]] = relationship(back_populates="market")
    bulletins: Mapped[list["MarketBulletin"]] = relationship(back_populates="market")




class MarketBulletin(Base):
    __tablename__ = "market_bulletins"

    id: Mapped[int] = mapped_column(primary_key=True)
    market_id: Mapped[Optional[int]] = mapped_column(ForeignKey("markets.id"), nullable=True, index=True)
    country_code: Mapped[Optional[str]] = mapped_column(String(8), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(200), index=True)
    body: Mapped[str] = mapped_column(Text)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(60), default="notice", index=True)
    channels_csv: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    audience: Mapped[str] = mapped_column(String(60), default="customer")
    severity: Mapped[str] = mapped_column(String(40), default="info")
    auto_inject_to_ai: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    starts_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    ends_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)

    market: Mapped[Optional["Market"]] = relationship(back_populates="bulletins")

class AIConfigResource(Base):
    __tablename__ = "ai_config_resources"

    id: Mapped[int] = mapped_column(primary_key=True)
    resource_key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    config_type: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    scope_type: Mapped[str] = mapped_column(String(40), default="global", index=True)
    scope_value: Mapped[Optional[str]] = mapped_column(String(160), nullable=True, index=True)
    market_id: Mapped[Optional[int]] = mapped_column(ForeignKey("markets.id"), nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    draft_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    draft_content_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    published_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    published_content_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    published_version: Mapped[int] = mapped_column(Integer, default=0)
    published_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    updated_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    published_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now, index=True)

    market: Mapped[Optional["Market"]] = relationship()
    versions: Mapped[list["AIConfigVersion"]] = relationship(back_populates="resource", cascade="all, delete-orphan")


class AIConfigVersion(Base):
    __tablename__ = "ai_config_versions"
    __table_args__ = (UniqueConstraint("resource_id", "version", name="uq_ai_config_resource_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    resource_id: Mapped[int] = mapped_column(ForeignKey("ai_config_resources.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, index=True)
    snapshot_json: Mapped[dict] = mapped_column(JSON)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    published_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    published_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)

    resource: Mapped["AIConfigResource"] = relationship(back_populates="versions")


class ChannelAccount(Base):
    __tablename__ = "channel_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    account_id: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    market_id: Mapped[Optional[int]] = mapped_column(ForeignKey("markets.id"), nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    health_status: Mapped[str] = mapped_column(String(40), default="unknown")
    last_health_check_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    fallback_account_id: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)

    market: Mapped[Optional["Market"]] = relationship(back_populates="channel_accounts")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    email: Mapped[Optional[str]] = mapped_column(String(200), unique=True, nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), index=True)
    team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id"), nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)

    team: Mapped[Optional["Team"]] = relationship(back_populates="users")



class IntegrationClient(Base):
    __tablename__ = "integration_clients"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    key_id: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    secret_hash: Mapped[str] = mapped_column(String(255))
    scopes_csv: Mapped[str] = mapped_column(Text, default="profile.read,task.write")
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, default=60)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class IntegrationRequestLog(Base):
    __tablename__ = "integration_request_logs"
    __table_args__ = (UniqueConstraint("client_id", "endpoint", "idempotency_key", name="uq_integration_client_endpoint_idem"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[Optional[int]] = mapped_column(ForeignKey("integration_clients.id"), nullable=True, index=True)
    endpoint: Mapped[str] = mapped_column(String(160), index=True)
    method: Mapped[str] = mapped_column(String(16), default="GET")
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(160), nullable=True, index=True)
    request_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    response_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)

    client: Mapped[Optional["IntegrationClient"]] = relationship()


class AuthThrottleEntry(Base):
    __tablename__ = "auth_throttle_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    throttle_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    last_failed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)



class WebchatRateLimitBucket(Base):
    __tablename__ = "webchat_rate_limits"

    id: Mapped[int] = mapped_column(primary_key=True)
    bucket_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    window_start: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    request_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now, nullable=False)


class UserCapabilityOverride(Base):
    __tablename__ = "user_capability_overrides"
    __table_args__ = (UniqueConstraint("user_id", "capability", name="uq_user_capability_override"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    capability: Mapped[str] = mapped_column(String(120), index=True)
    allowed: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)

    user: Mapped["User"] = relationship()


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(120), index=True)
    target_type: Mapped[str] = mapped_column(String(80), index=True)
    target_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    old_value_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    new_value_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)


class OpenClawUnresolvedEvent(Base):
    __tablename__ = "openclaw_unresolved_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(80), index=True)
    session_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    event_type: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    recipient: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    source_chat_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    preferred_reply_contact: Mapped[Optional[str]] = mapped_column(String(160), nullable=True, index=True)
    payload_json: Mapped[str] = mapped_column(Text)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True, default=_openclaw_payload_hash_default)
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    replay_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now, index=True)


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    email: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, index=True)
    email_normalized: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, index=True)
    phone: Mapped[Optional[str]] = mapped_column(String(60), nullable=True, index=True)
    phone_normalized: Mapped[Optional[str]] = mapped_column(String(60), nullable=True, index=True)
    external_ref: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)

    tickets: Mapped[list["Ticket"]] = relationship(back_populates="customer")




class ServiceHeartbeat(Base):
    __tablename__ = "service_heartbeats"

    id: Mapped[int] = mapped_column(primary_key=True)
    service_name: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    instance_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="unknown")
    details_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class SLAPolicy(Base):
    __tablename__ = "sla_policies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    priority: Mapped[TicketPriority] = mapped_column(Enum(TicketPriority), unique=True, index=True)
    first_response_minutes: Mapped[int] = mapped_column(Integer)
    resolution_minutes: Mapped[int] = mapped_column(Integer)
    pause_on_waiting_customer: Mapped[bool] = mapped_column(Boolean, default=True)
    pause_on_waiting_internal: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_no: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255), index=True)
    description: Mapped[str] = mapped_column(Text)
    customer_id: Mapped[Optional[int]] = mapped_column(ForeignKey("customers.id"), nullable=True, index=True)
    source: Mapped[TicketSource] = mapped_column(Enum(TicketSource), index=True)
    source_channel: Mapped[SourceChannel] = mapped_column(Enum(SourceChannel), index=True)
    priority: Mapped[TicketPriority] = mapped_column(Enum(TicketPriority), index=True)
    status: Mapped[TicketStatus] = mapped_column(Enum(TicketStatus), index=True, default=TicketStatus.new)
    category: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    sub_category: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    tracking_number: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    assignee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id"), nullable=True, index=True)
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    market_id: Mapped[Optional[int]] = mapped_column(ForeignKey("markets.id"), nullable=True, index=True)
    country_code: Mapped[Optional[str]] = mapped_column(String(8), nullable=True, index=True)
    conversation_state: Mapped[ConversationState] = mapped_column(Enum(ConversationState), default=ConversationState.ai_active, index=True)
    channel_account_id: Mapped[Optional[int]] = mapped_column(ForeignKey("channel_accounts.id"), nullable=True, index=True)
    sla_policy_id: Mapped[Optional[int]] = mapped_column(ForeignKey("sla_policies.id"), nullable=True)

    first_response_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    first_response_due_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    resolution_due_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    reopen_count: Mapped[int] = mapped_column(Integer, default=0)

    resolution_category: Mapped[ResolutionCategory] = mapped_column(
        Enum(ResolutionCategory), default=ResolutionCategory.none, index=True
    )

    sla_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    sla_paused_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    sla_pause_reason: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    total_paused_seconds: Mapped[int] = mapped_column(Integer, default=0)
    first_response_breached: Mapped[bool] = mapped_column(Boolean, default=False)
    resolution_breached: Mapped[bool] = mapped_column(Boolean, default=False)

    ai_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ai_classification: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    ai_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    case_type: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    issue_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    customer_request: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_chat_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    source_dedupe_key: Mapped[Optional[str]] = mapped_column(String(300), nullable=True, index=True)
    required_action: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    missing_fields: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_customer_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    customer_update: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resolution_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_human_update: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    requested_time: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    destination: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    preferred_reply_channel: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    preferred_reply_contact: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now, index=True)

    customer: Mapped[Optional["Customer"]] = relationship(back_populates="tickets")
    assignee: Mapped[Optional["User"]] = relationship(foreign_keys=[assignee_id])
    creator: Mapped[Optional["User"]] = relationship(foreign_keys=[created_by])
    team: Mapped[Optional["Team"]] = relationship(foreign_keys=[team_id])
    market: Mapped[Optional["Market"]] = relationship(foreign_keys=[market_id])
    sla_policy: Mapped[Optional["SLAPolicy"]] = relationship()
    channel_account: Mapped[Optional["ChannelAccount"]] = relationship()

    comments: Mapped[list["TicketComment"]] = relationship(back_populates="ticket", cascade="all, delete-orphan")
    internal_notes: Mapped[list["TicketInternalNote"]] = relationship(back_populates="ticket", cascade="all, delete-orphan")
    events: Mapped[list["TicketEvent"]] = relationship(back_populates="ticket", cascade="all, delete-orphan")
    attachments: Mapped[list["TicketAttachment"]] = relationship(back_populates="ticket", cascade="all, delete-orphan")
    outbound_messages: Mapped[list["TicketOutboundMessage"]] = relationship(back_populates="ticket", cascade="all, delete-orphan")
    ai_intakes: Mapped[list["TicketAIIntake"]] = relationship(back_populates="ticket", cascade="all, delete-orphan")
    openclaw_link: Mapped[Optional["OpenClawConversationLink"]] = relationship(back_populates="ticket", uselist=False, cascade="all, delete-orphan")
    openclaw_attachment_references: Mapped[list["OpenClawAttachmentReference"]] = relationship(back_populates="ticket", cascade="all, delete-orphan")


class TicketComment(Base):
    __tablename__ = "ticket_comments"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    author_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    body: Mapped[str] = mapped_column(Text)
    visibility: Mapped[NoteVisibility] = mapped_column(Enum(NoteVisibility), default=NoteVisibility.external)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)

    ticket: Mapped["Ticket"] = relationship(back_populates="comments")
    author: Mapped[Optional["User"]] = relationship()


class TicketInternalNote(Base):
    __tablename__ = "ticket_internal_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    author_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)

    ticket: Mapped["Ticket"] = relationship(back_populates="internal_notes")
    author: Mapped[Optional["User"]] = relationship()


class TicketAttachment(Base):
    __tablename__ = "ticket_attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    uploaded_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    file_name: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    file_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    file_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    mime_type: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    file_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    visibility: Mapped[NoteVisibility] = mapped_column(Enum(NoteVisibility), default=NoteVisibility.external)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)

    ticket: Mapped["Ticket"] = relationship(back_populates="attachments")
    uploader: Mapped[Optional["User"]] = relationship()

    @property
    def download_url(self) -> str:
        return self.file_url or f"/api/files/{self.id}/download"


class TicketEvent(Base):
    __tablename__ = "ticket_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    actor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    event_type: Mapped[EventType] = mapped_column(Enum(EventType), index=True)
    field_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    old_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    new_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)

    ticket: Mapped["Ticket"] = relationship(back_populates="events")
    actor: Mapped[Optional["User"]] = relationship()


class TicketOutboundMessage(Base):
    __tablename__ = "ticket_outbound_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    channel: Mapped[SourceChannel] = mapped_column(Enum(SourceChannel), index=True)
    status: Mapped[MessageStatus] = mapped_column(Enum(MessageStatus), index=True)
    body: Mapped[str] = mapped_column(Text)
    provider_status: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    last_attempt_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    locked_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    locked_by: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    provider_message_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    failure_code: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    failure_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)

    ticket: Mapped["Ticket"] = relationship(back_populates="outbound_messages")
    creator: Mapped[Optional["User"]] = relationship()


class TicketAIIntake(Base):
    __tablename__ = "ticket_ai_intakes"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    summary: Mapped[str] = mapped_column(Text)
    classification: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    missing_fields_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    recommended_action: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    suggested_reply: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    human_override_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    market_id: Mapped[Optional[int]] = mapped_column(ForeignKey("markets.id"), nullable=True, index=True)
    country_code: Mapped[Optional[str]] = mapped_column(String(8), nullable=True, index=True)
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)

    ticket: Mapped["Ticket"] = relationship(back_populates="ai_intakes")
    creator: Mapped[Optional["User"]] = relationship()


class BackgroundJob(Base):
    __tablename__ = "background_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    queue_name: Mapped[str] = mapped_column(String(80), index=True)
    job_type: Mapped[str] = mapped_column(String(120), index=True)
    payload_json: Mapped[str] = mapped_column(Text)
    dedupe_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), index=True, default=JobStatus.pending)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    next_run_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    locked_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    locked_by: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)




class OpenClawConversationLink(Base):
    __tablename__ = "openclaw_conversation_links"
    __table_args__ = (
        UniqueConstraint("session_key", name="uq_openclaw_session_key"),
        UniqueConstraint("ticket_id", name="uq_openclaw_ticket_link"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    session_key: Mapped[str] = mapped_column(String(255), index=True)
    channel: Mapped[Optional[str]] = mapped_column(String(60), nullable=True, index=True)
    recipient: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    account_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    thread_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    channel_account_id: Mapped[Optional[int]] = mapped_column(ForeignKey("channel_accounts.id"), nullable=True, index=True)
    route_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    last_cursor: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_message_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)

    ticket: Mapped["Ticket"] = relationship(back_populates="openclaw_link")
    channel_account: Mapped[Optional["ChannelAccount"]] = relationship()
    transcript_messages: Mapped[list["OpenClawTranscriptMessage"]] = relationship(back_populates="conversation", cascade="all, delete-orphan")


class OpenClawTranscriptMessage(Base):
    __tablename__ = "openclaw_transcript_messages"
    __table_args__ = (UniqueConstraint("conversation_id", "message_id", name="uq_openclaw_conversation_message"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("openclaw_conversation_links.id"), index=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    session_key: Mapped[str] = mapped_column(String(255), index=True)
    message_id: Mapped[str] = mapped_column(String(255), index=True)
    role: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    author_name: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    body_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    content_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    received_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)

    conversation: Mapped["OpenClawConversationLink"] = relationship(back_populates="transcript_messages")
    ticket: Mapped["Ticket"] = relationship()


class OpenClawAttachmentReference(Base):
    __tablename__ = "openclaw_attachment_references"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("openclaw_conversation_links.id"), index=True)
    transcript_message_id: Mapped[int] = mapped_column(ForeignKey("openclaw_transcript_messages.id"), index=True)
    remote_attachment_id: Mapped[str] = mapped_column(String(160), index=True)
    content_type: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    metadata_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    storage_status: Mapped[str] = mapped_column(String(40), default="referenced")
    storage_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)

    ticket: Mapped["Ticket"] = relationship(back_populates="openclaw_attachment_references")
    conversation: Mapped["OpenClawConversationLink"] = relationship()
    transcript_message: Mapped["OpenClawTranscriptMessage"] = relationship()


class OpenClawSyncCursor(Base):
    __tablename__ = "openclaw_sync_cursors"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    cursor_value: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    color: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)


class TicketTag(Base):
    __tablename__ = "ticket_tags"
    __table_args__ = (UniqueConstraint("ticket_id", "tag_id", name="uq_ticket_tag"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id"), index=True)


class TicketFollower(Base):
    __tablename__ = "ticket_followers"
    __table_args__ = (UniqueConstraint("ticket_id", "user_id", name="uq_ticket_follower"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
