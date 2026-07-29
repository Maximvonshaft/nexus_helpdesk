from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .utils.time import utc_now


UTCDateTime = DateTime(timezone=True)

WHATSAPP_TRANSPORTS = ("baileys_sidecar", "meta_cloud_api")
WHATSAPP_DESIRED_STATES = ("disabled", "binding", "active")
WHATSAPP_OBSERVED_STATES = (
    "unconfigured",
    "auth_required",
    "qr_pending",
    "auth_persisting",
    "connecting",
    "connected",
    "degraded",
    "logged_out",
    "error",
    "disabled",
)
WHATSAPP_AUTHENTICATION_STATES = (
    "unconfigured",
    "pending",
    "linked",
    "unstable",
    "revoked",
    "error",
)
WHATSAPP_LISTENER_STATES = (
    "stopped",
    "starting",
    "active",
    "reconnecting",
    "error",
)
WHATSAPP_VERIFICATION_STATES = (
    "pending",
    "inbound_verified",
    "outbound_verified",
    "verified",
    "failed",
)
WHATSAPP_MEDIA_STORAGE_STATES = (
    "pending",
    "downloading",
    "scanning",
    "available",
    "quarantined",
    "rejected",
    "failed",
    "deleted",
)
WHATSAPP_MEDIA_SCAN_STATES = (
    "pending",
    "clean",
    "infected",
    "unavailable",
    "failed",
)
WHATSAPP_SIGNUP_STATES = (
    "pending",
    "exchanging",
    "completed",
    "expired",
    "failed",
    "cancelled",
)


class WhatsAppConnection(Base):
    """One-to-one WhatsApp runtime/configuration extension of ChannelAccount."""

    __tablename__ = "whatsapp_connections"
    __table_args__ = (
        UniqueConstraint(
            "channel_account_id",
            name="uq_whatsapp_connections_channel_account",
        ),
        CheckConstraint(
            "transport IN ('baileys_sidecar','meta_cloud_api')",
            name="ck_whatsapp_connection_transport",
        ),
        CheckConstraint(
            "desired_state IN ('disabled','binding','active')",
            name="ck_whatsapp_connection_desired_state",
        ),
        CheckConstraint(
            "observed_state IN ("
            "'unconfigured','auth_required','qr_pending','auth_persisting',"
            "'connecting','connected','degraded','logged_out','error','disabled'"
            ")",
            name="ck_whatsapp_connection_observed_state",
        ),
        CheckConstraint(
            "authentication_state IN ("
            "'unconfigured','pending','linked','unstable','revoked','error'"
            ")",
            name="ck_whatsapp_connection_authentication_state",
        ),
        CheckConstraint(
            "listener_state IN ("
            "'stopped','starting','active','reconnecting','error'"
            ")",
            name="ck_whatsapp_connection_listener_state",
        ),
        CheckConstraint(
            "verification_state IN ("
            "'pending','inbound_verified','outbound_verified','verified','failed'"
            ")",
            name="ck_whatsapp_connection_verification_state",
        ),
        CheckConstraint(
            "desired_generation >= 0 AND observed_generation >= 0",
            name="ck_whatsapp_connection_generations_nonnegative",
        ),
        CheckConstraint(
            "reconnect_count >= 0",
            name="ck_whatsapp_connection_reconnect_count_nonnegative",
        ),
        CheckConstraint(
            "transport <> 'meta_cloud_api' OR phone_number_id IS NOT NULL",
            name="ck_whatsapp_meta_phone_number_id_required",
        ),
        CheckConstraint(
            "transport <> 'meta_cloud_api' OR waba_id IS NOT NULL",
            name="ck_whatsapp_meta_waba_id_required",
        ),
        Index(
            "ix_whatsapp_connection_tenant_transport_state",
            "tenant_id",
            "transport",
            "desired_state",
            "observed_state",
        ),
        Index(
            "ix_whatsapp_connection_probe",
            "desired_state",
            "last_probe_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    channel_account_id: Mapped[int] = mapped_column(
        ForeignKey("channel_accounts.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    transport: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    desired_state: Mapped[str] = mapped_column(
        String(24), nullable=False, default="disabled", index=True
    )
    observed_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unconfigured", index=True
    )
    authentication_state: Mapped[str] = mapped_column(
        String(24), nullable=False, default="unconfigured", index=True
    )
    listener_state: Mapped[str] = mapped_column(
        String(24), nullable=False, default="stopped", index=True
    )
    verification_state: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", index=True
    )
    desired_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    observed_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    phone_number: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    jid: Mapped[Optional[str]] = mapped_column(String(180), nullable=True)
    business_account_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    waba_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    phone_number_id: Mapped[Optional[str]] = mapped_column(
        String(120), nullable=True, unique=True, index=True
    )
    graph_api_version: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    access_token_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    access_token_fingerprint: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    app_secret_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    verify_token_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sidecar_session_key: Mapped[Optional[str]] = mapped_column(String(180), nullable=True)
    session_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_qr_generated_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True
    )
    qr_expires_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    last_connected_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    last_disconnected_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    last_inbound_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    last_outbound_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    last_probe_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    last_probe_status: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    reconnect_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_code: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    last_error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    inbound_tested_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    outbound_tested_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    verified_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    created_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    updated_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        index=True,
    )

    channel_account = relationship("ChannelAccount")
    tenant = relationship("Tenant")


class WhatsAppMediaAsset(Base):
    """Provider media evidence that projects only clean bytes into TicketAttachment."""

    __tablename__ = "whatsapp_media_assets"
    __table_args__ = (
        UniqueConstraint(
            "connection_id",
            "provider",
            "provider_media_id",
            name="uq_whatsapp_media_connection_provider_id",
        ),
        CheckConstraint(
            "provider IN ('baileys','meta')",
            name="ck_whatsapp_media_provider",
        ),
        CheckConstraint(
            "storage_status IN ("
            "'pending','downloading','scanning','available','quarantined',"
            "'rejected','failed','deleted'"
            ")",
            name="ck_whatsapp_media_storage_status",
        ),
        CheckConstraint(
            "scan_status IN ('pending','clean','infected','unavailable','failed')",
            name="ck_whatsapp_media_scan_status",
        ),
        CheckConstraint(
            "byte_size IS NULL OR byte_size >= 0",
            name="ck_whatsapp_media_byte_size_nonnegative",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND max_attempts >= 1",
            name="ck_whatsapp_media_attempts_valid",
        ),
        Index(
            "ix_whatsapp_media_tenant_status",
            "tenant_id",
            "storage_status",
            "created_at",
        ),
        Index(
            "ix_whatsapp_media_claim",
            "provider",
            "storage_status",
            "next_retry_at",
            "locked_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    connection_id: Mapped[int] = mapped_column(
        ForeignKey("whatsapp_connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    inbound_message_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("whatsapp_inbound_messages.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    outbound_message_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ticket_outbound_messages.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    provider_media_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    media_kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    file_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    declared_mime_type: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    detected_mime_type: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    byte_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    storage_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", index=True
    )
    scan_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="pending", index=True
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True, index=True
    )
    locked_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True, index=True
    )
    locked_by: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    storage_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    ticket_attachment_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ticket_attachments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    provider_url_expires_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True
    )
    last_error_code: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    last_error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    downloaded_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    scanned_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    available_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        index=True,
    )

    connection = relationship("WhatsAppConnection")
    inbound_message = relationship("WhatsAppInboundMessage")
    outbound_message = relationship("TicketOutboundMessage")
    ticket_attachment = relationship("TicketAttachment")
    tenant = relationship("Tenant")


class WhatsAppEmbeddedSignupSession(Base):
    """One-time, tenant-scoped Meta Embedded Signup code-exchange session."""

    __tablename__ = "whatsapp_embedded_signup_sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN ("
            "'pending','exchanging','completed','expired','failed','cancelled'"
            ")",
            name="ck_whatsapp_embedded_signup_status",
        ),
        Index(
            "ix_whatsapp_embedded_signup_tenant_status",
            "tenant_id",
            "status",
            "expires_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    requested_by: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    state_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="pending", index=True
    )
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    connection_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("whatsapp_connections.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
        index=True,
    )
    code_fingerprint: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    business_account_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    waba_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    phone_number_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    last_error_code: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        index=True,
    )

    tenant = relationship("Tenant")
    requester = relationship("User")
    connection = relationship("WhatsAppConnection")
