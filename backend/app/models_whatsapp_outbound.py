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


class WhatsAppOutboundPart(Base):
    """Ordered provider messages emitted by one canonical Outbox message."""

    __tablename__ = "whatsapp_outbound_parts"
    __table_args__ = (
        UniqueConstraint(
            "outbound_message_id",
            "sequence",
            name="uq_whatsapp_outbound_part_sequence",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_whatsapp_outbound_part_idempotency",
        ),
        CheckConstraint(
            "part_type IN ('text','media')",
            name="ck_whatsapp_outbound_part_type",
        ),
        CheckConstraint(
            "status IN ("
            "'queued','accepted','sent','delivered','read','failed','expired','revoked'"
            ")",
            name="ck_whatsapp_outbound_part_status",
        ),
        CheckConstraint(
            "sequence >= 0",
            name="ck_whatsapp_outbound_part_sequence_nonnegative",
        ),
        Index(
            "ix_whatsapp_outbound_part_parent_status",
            "outbound_message_id",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    connection_id: Mapped[int] = mapped_column(
        ForeignKey("whatsapp_connections.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    outbound_message_id: Mapped[int] = mapped_column(
        ForeignKey("ticket_outbound_messages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    attachment_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ticket_attachments.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    part_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    media_kind: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    media_type: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    file_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="queued", index=True
    )
    provider_media_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    provider_message_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, unique=True, index=True
    )
    failure_code: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    failure_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    read_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    receipt_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
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
    connection = relationship("WhatsAppConnection")
    outbound_message = relationship("TicketOutboundMessage")
    attachment = relationship("TicketAttachment")
