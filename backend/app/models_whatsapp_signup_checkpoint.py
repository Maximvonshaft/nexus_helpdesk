from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .utils.time import utc_now


UTCDateTime = DateTime(timezone=True)


class WhatsAppEmbeddedSignupExchangeCheckpoint(Base):
    """Encrypted post-OAuth checkpoint for one Embedded Signup session."""

    __tablename__ = "whatsapp_embedded_signup_exchange_checkpoints"

    session_id: Mapped[str] = mapped_column(
        ForeignKey(
            "whatsapp_embedded_signup_sessions.id",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    encrypted_access_token: Mapped[str] = mapped_column(Text, nullable=False)
    exchanged_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        default=utc_now,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        default=utc_now,
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )

    session = relationship("WhatsAppEmbeddedSignupSession")
