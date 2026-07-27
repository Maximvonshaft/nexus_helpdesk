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


class WhatsAppConnection(Base):
    """One-to-one WhatsApp runtime/configuration extension of ChannelAccount.

    ``ChannelAccount`` remains the sole channel identity and routing authority.
    This record owns transport-specific desired state, encrypted credentials and
    observed runtime evidence; it is not a second channel-account product.
    """

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
        String(24),
        nullable=False,
        default="disabled",
        index=True,
    )
    observed_state: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="unconfigured",
        index=True,
    )
    authentication_state: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="unconfigured",
        index=True,
    )
    listener_state: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="stopped",
        index=True,
    )
    verification_state: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",
        index=True,
    )
    desired_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    observed_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    phone_number: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    jid: Mapped[Optional[str]] = mapped_column(String(180), nullable=True)

    business_account_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    waba_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    phone_number_id: Mapped[Optional[str]] = mapped_column(
        String(120),
        nullable=True,
        unique=True,
        index=True,
    )
    graph_api_version: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    access_token_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    access_token_fingerprint: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    app_secret_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    verify_token_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    sidecar_session_key: Mapped[Optional[str]] = mapped_column(String(180), nullable=True)
    session_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    last_qr_generated_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime,
        nullable=True,
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
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    updated_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        default=utc_now,
        index=True,
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
