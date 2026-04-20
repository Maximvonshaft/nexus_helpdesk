from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .utils.time import utc_now

UTCDateTime = DateTime(timezone=True)


class WebChatSite(Base):
    __tablename__ = "webchat_sites"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    widget_title: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    logo_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    welcome_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    default_language: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    allowed_origins_csv: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    theme_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    business_hours_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    mapped_market_id: Mapped[Optional[int]] = mapped_column(ForeignKey("markets.id"), nullable=True, index=True)
    mapped_team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id"), nullable=True, index=True)
    mapped_openclaw_agent: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)

    sessions: Mapped[list["WebChatSession"]] = relationship(back_populates="site", cascade="all, delete-orphan")


class WebChatSession(Base):
    __tablename__ = "webchat_sessions"
    __table_args__ = (
        UniqueConstraint("browser_session_id", name="uq_webchat_browser_session"),
        UniqueConstraint("openclaw_session_key", name="uq_webchat_openclaw_session_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("webchat_sites.id"), index=True)
    ticket_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tickets.id"), nullable=True, index=True)
    visitor_id: Mapped[str] = mapped_column(String(160), index=True)
    browser_session_id: Mapped[str] = mapped_column(String(160), index=True)
    openclaw_session_key: Mapped[str] = mapped_column(String(255), index=True)
    origin: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    locale: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    timezone: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="active", index=True)
    handoff_status: Mapped[str] = mapped_column(String(40), default="none", index=True)
    last_message_preview: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_message_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    last_seen_message_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now, index=True)
    last_active_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)

    site: Mapped["WebChatSite"] = relationship(back_populates="sessions")
    handoffs: Mapped[list["WebChatHandoffRequest"]] = relationship(back_populates="session", cascade="all, delete-orphan")
    ticket_upgrades: Mapped[list["WebChatTicketUpgradeLink"]] = relationship(back_populates="session", cascade="all, delete-orphan")


class WebChatHandoffRequest(Base):
    __tablename__ = "webchat_handoff_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("webchat_sessions.id"), index=True)
    requested_by: Mapped[str] = mapped_column(String(40), default="visitor")
    status: Mapped[str] = mapped_column(String(40), default="queued", index=True)
    reason: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    assigned_to_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    created_ticket_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tickets.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)

    session: Mapped["WebChatSession"] = relationship(back_populates="handoffs")


class WebChatTicketUpgradeLink(Base):
    __tablename__ = "webchat_ticket_upgrade_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("webchat_sessions.id"), index=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    upgrade_type: Mapped[str] = mapped_column(String(40), default="session_to_ticket")
    created_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)

    session: Mapped["WebChatSession"] = relationship(back_populates="ticket_upgrades")


class WebChatAuditLog(Base):
    __tablename__ = "webchat_audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[Optional[int]] = mapped_column(ForeignKey("webchat_sites.id"), nullable=True, index=True)
    session_id: Mapped[Optional[int]] = mapped_column(ForeignKey("webchat_sessions.id"), nullable=True, index=True)
    ticket_id: Mapped[Optional[int]] = mapped_column(ForeignKey("tickets.id"), nullable=True, index=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    request_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(String(40), default="ok", index=True)
    origin: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    ip_hash: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    payload_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
