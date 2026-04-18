from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .utils.time import utc_now

UTCDateTime = DateTime(timezone=True)


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(40), default="active", index=True)
    external_ref: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now, index=True)

    memberships: Mapped[list["TenantMembership"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")
    ai_profile: Mapped[Optional["TenantAIProfile"]] = relationship(back_populates="tenant", uselist=False, cascade="all, delete-orphan")
    knowledge_entries: Mapped[list["TenantKnowledgeEntry"]] = relationship(back_populates="tenant", cascade="all, delete-orphan")


class TenantMembership(Base):
    __tablename__ = "tenant_memberships"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", name="uq_tenant_membership"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    membership_role: Mapped[str] = mapped_column(String(40), default="member", index=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now, index=True)

    tenant: Mapped["Tenant"] = relationship(back_populates="memberships")


class TenantAIProfile(Base):
    __tablename__ = "tenant_ai_profiles"
    __table_args__ = (UniqueConstraint("tenant_id", name="uq_tenant_ai_profile"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    display_name: Mapped[str] = mapped_column(String(160), default="Support Assistant")
    brand_name: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    role_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tone_style: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    forbidden_claims: Mapped[Optional[list[str]]] = mapped_column(JSON, nullable=True)
    escalation_policy: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    signature_style: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    language_policy: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    system_prompt_overrides: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    system_context: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    enable_auto_reply: Mapped[bool] = mapped_column(Boolean, default=True)
    enable_auto_summary: Mapped[bool] = mapped_column(Boolean, default=True)
    enable_auto_classification: Mapped[bool] = mapped_column(Boolean, default=True)
    allowed_actions: Mapped[Optional[list[str]]] = mapped_column(JSON, nullable=True)
    default_model_key: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now, index=True)

    tenant: Mapped["Tenant"] = relationship(back_populates="ai_profile")


class TenantKnowledgeEntry(Base):
    __tablename__ = "tenant_knowledge_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    title: Mapped[str] = mapped_column(String(200), index=True)
    category: Mapped[str] = mapped_column(String(80), default="faq", index=True)
    content: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(60), default="manual", index=True)
    source_ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=100, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    tags_json: Mapped[Optional[list[str]]] = mapped_column(JSON, nullable=True)
    metadata_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now, index=True)

    tenant: Mapped["Tenant"] = relationship(back_populates="knowledge_entries")


class TicketTenantLink(Base):
    __tablename__ = "ticket_tenant_links"
    __table_args__ = (
        UniqueConstraint("ticket_id", name="uq_ticket_tenant_ticket"),
        UniqueConstraint("tenant_id", "ticket_id", name="uq_ticket_tenant_pair"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)


class CustomerTenantLink(Base):
    __tablename__ = "customer_tenant_links"
    __table_args__ = (UniqueConstraint("tenant_id", "customer_id", name="uq_customer_tenant_pair"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)


class TeamTenantLink(Base):
    __tablename__ = "team_tenant_links"
    __table_args__ = (
        UniqueConstraint("team_id", name="uq_team_tenant_team"),
        UniqueConstraint("tenant_id", "team_id", name="uq_team_tenant_pair"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)


class ChannelAccountTenantLink(Base):
    __tablename__ = "channel_account_tenant_links"
    __table_args__ = (
        UniqueConstraint("channel_account_id", name="uq_channel_account_tenant_account"),
        UniqueConstraint("tenant_id", "channel_account_id", name="uq_channel_account_tenant_pair"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    channel_account_id: Mapped[int] = mapped_column(ForeignKey("channel_accounts.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)


class MarketBulletinTenantLink(Base):
    __tablename__ = "market_bulletin_tenant_links"
    __table_args__ = (
        UniqueConstraint("bulletin_id", name="uq_market_bulletin_tenant_bulletin"),
        UniqueConstraint("tenant_id", "bulletin_id", name="uq_market_bulletin_tenant_pair"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    bulletin_id: Mapped[int] = mapped_column(ForeignKey("market_bulletins.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)


class AIConfigResourceTenantLink(Base):
    __tablename__ = "ai_config_resource_tenant_links"
    __table_args__ = (
        UniqueConstraint("resource_id", name="uq_ai_config_resource_tenant_resource"),
        UniqueConstraint("tenant_id", "resource_id", name="uq_ai_config_resource_tenant_pair"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    resource_id: Mapped[int] = mapped_column(ForeignKey("ai_config_resources.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)


class OpenClawConversationTenantLink(Base):
    __tablename__ = "openclaw_conversation_tenant_links"
    __table_args__ = (
        UniqueConstraint("conversation_id", name="uq_openclaw_conversation_tenant_conversation"),
        UniqueConstraint("tenant_id", "conversation_id", name="uq_openclaw_conversation_tenant_pair"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("openclaw_conversation_links.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
