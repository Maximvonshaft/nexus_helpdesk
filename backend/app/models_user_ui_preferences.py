from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .utils.time import utc_now

UTCDateTime = DateTime(timezone=True)
SUPPORTED_UI_LOCALES = ("zh-CN", "en", "de")


class UserUIPreference(Base):
    __tablename__ = "user_ui_preferences"
    __table_args__ = (
        CheckConstraint(
            "ui_locale IN ('zh-CN','en','de')",
            name="ck_user_ui_preferences_locale",
        ),
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    ui_locale: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="zh-CN",
        server_default="zh-CN",
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
