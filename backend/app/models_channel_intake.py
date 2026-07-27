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


class CustomerIdentityBinding(Base):
    """Canonical Tenant-scoped external identity binding for one Customer.

    Customer remains the profile aggregate. This table is the sole atomic lookup
    authority for channel identities and prevents global or race-prone Customer
    discovery from leaking across Tenant boundaries.
    """

    __tablename__ = "customer_identity_bindings"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "identity_type",
            "normalized_value",
            name="uq_customer_identity_tenant_type_value",
        ),
        CheckConstraint(
            "identity_type IN ('email','phone','external_ref')",
            name="ck_customer_identity_type",
        ),
        CheckConstraint(
            "length(trim(normalized_value)) > 0",
            name="ck_customer_identity_value_nonempty",
        ),
        Index(
            "ix_customer_identity_customer",
            "tenant_id",
            "customer_id",
            "identity_type",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    identity_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    normalized_value: Mapped[str] = mapped_column(String(320), nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
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
    )

    customer = relationship("Customer")


class EmailIntakeQuarantine(Base):
    """Durable, recoverable intake record for an Email not yet bound to a Case.

    The IMAP cursor may advance only after the message is either projected into
    the canonical Conversation intake or persisted here. Quarantine is not a
    second queue: it is the failure state of the same channel-intake contract.
    """

    __tablename__ = "email_intake_quarantine"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "provider_message_id",
            name="uq_email_intake_quarantine_account_provider_message",
        ),
        CheckConstraint(
            "status IN ('pending_intake','projected','rejected')",
            name="ck_email_intake_quarantine_status",
        ),
        Index(
            "ix_email_intake_quarantine_tenant_status",
            "tenant_id",
            "status",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey("outbound_email_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider_message_id: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True
    )
    mailbox_uid: Mapped[str] = mapped_column(String(80), nullable=False)
    from_address: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    from_name: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    to_address: Mapped[Optional[str]] = mapped_column(String(320), nullable=True)
    cc: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    subject: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    mailbox_message_id: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    mailbox_references: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    in_reply_to: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    received_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending_intake", index=True
    )
    reason_code: Mapped[str] = mapped_column(
        String(80), nullable=False, default="ticket_not_resolved"
    )
    conversation_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    ticket_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True, index=True
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
    )

    account = relationship("OutboundEmailAccount")
    ticket = relationship("Ticket")
