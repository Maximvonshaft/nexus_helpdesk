from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import UserDefinedType

from .db import Base
from .utils.time import utc_now

UTCDateTime = DateTime(timezone=True)
KNOWLEDGE_VECTOR_DIMENSION = 384


class PGVector(UserDefinedType):
    cache_ok = True

    def __init__(self, dimensions: int = KNOWLEDGE_VECTOR_DIMENSION):
        if isinstance(dimensions, bool) or not isinstance(dimensions, int) or dimensions <= 0:
            raise ValueError("PGVector dimensions must be a positive integer")
        self.dimensions = dimensions

    def get_col_spec(self, **_kw) -> str:
        return f"vector({self.dimensions})"


class PersonaProfile(Base):
    __tablename__ = "persona_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    market_id: Mapped[Optional[int]] = mapped_column(ForeignKey("markets.id"), nullable=True, index=True)
    channel: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    language: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True)
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


class PersonaProfileVersion(Base):
    __tablename__ = "persona_profile_versions"
    __table_args__ = (UniqueConstraint("profile_id", "version", name="uq_persona_profile_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("persona_profiles.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, index=True)
    snapshot_json: Mapped[dict] = mapped_column(JSON)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    published_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    published_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)


class PersonaProfileReview(Base):
    __tablename__ = "persona_profile_reviews"
    __table_args__ = (UniqueConstraint("profile_id", "review_version", name="uq_persona_profile_review_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("persona_profiles.id"), index=True)
    review_version: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    snapshot_json: Mapped[dict] = mapped_column(JSON)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    requested_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    reviewed_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    decision_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    release_window_start: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    release_window_end: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    published_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    published_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now, index=True)


class KnowledgeItem(Base):
    __tablename__ = "knowledge_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_key: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200), index=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="draft", index=True)
    source_type: Mapped[str] = mapped_column(String(20), default="text", index=True)
    knowledge_kind: Mapped[str] = mapped_column(String(40), default="document", index=True)
    tenant_id: Mapped[str] = mapped_column(String(80), default="default", index=True)
    brand_id: Mapped[str] = mapped_column(String(80), default="default", index=True)
    country_scope: Mapped[str] = mapped_column(String(16), default="GLOBAL", index=True)
    channel_scope: Mapped[str] = mapped_column(String(40), default="all", index=True)
    locale: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True)
    visibility: Mapped[str] = mapped_column(String(40), default="customer", index=True)
    shareability: Mapped[str] = mapped_column(String(40), default="customer_visible", index=True)
    authority_level: Mapped[str] = mapped_column(String(40), default="faq", index=True)
    risk_level: Mapped[str] = mapped_column(String(40), default="low", index=True)
    review_due_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    valid_from: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    valid_until: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    market_id: Mapped[Optional[int]] = mapped_column(ForeignKey("markets.id"), nullable=True, index=True)
    channel: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    audience_scope: Mapped[str] = mapped_column(String(40), default="customer", index=True)
    language: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=100, index=True)
    starts_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    ends_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    file_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    file_storage_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    mime_type: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    file_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    parsing_status: Mapped[str] = mapped_column(String(40), default="unparsed", index=True)
    parsing_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    parsed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    indexed_version: Mapped[int] = mapped_column(Integer, default=0, index=True)
    indexed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    fact_question: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    fact_answer: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    fact_aliases_json: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    fact_status: Mapped[str] = mapped_column(String(40), default="draft", index=True)
    answer_mode: Mapped[str] = mapped_column(String(40), default="guided_answer", index=True)
    citation_metadata_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    draft_body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    draft_normalized_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    published_body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    published_normalized_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    published_version: Mapped[int] = mapped_column(Integer, default=0)
    published_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    created_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    updated_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    published_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now, index=True)


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (UniqueConstraint("item_id", "published_version", "chunk_index", name="uq_knowledge_chunk_version_index"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("knowledge_items.id"), index=True)
    item_key: Mapped[str] = mapped_column(String(120), index=True)
    title: Mapped[str] = mapped_column(String(200))
    published_version: Mapped[int] = mapped_column(Integer, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, index=True)
    chunk_text: Mapped[str] = mapped_column(Text)
    normalized_text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    tenant_id: Mapped[str] = mapped_column(String(80), default="default", index=True)
    brand_id: Mapped[str] = mapped_column(String(80), default="default", index=True)
    country_scope: Mapped[str] = mapped_column(String(16), default="GLOBAL", index=True)
    channel_scope: Mapped[str] = mapped_column(String(40), default="all", index=True)
    locale: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True)
    visibility: Mapped[str] = mapped_column(String(40), default="customer", index=True)
    shareability: Mapped[str] = mapped_column(String(40), default="customer_visible", index=True)
    authority_level: Mapped[str] = mapped_column(String(40), default="faq", index=True)
    risk_level: Mapped[str] = mapped_column(String(40), default="low", index=True)
    review_due_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    valid_from: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    valid_until: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    market_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    channel: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    audience_scope: Mapped[str] = mapped_column(String(40), default="customer", index=True)
    language: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, index=True)
    starts_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    ends_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="active", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=100, index=True)
    source_type: Mapped[str] = mapped_column(String(20), default="text")
    knowledge_kind: Mapped[str] = mapped_column(String(40), default="document", index=True)
    fact_status: Mapped[str] = mapped_column(String(40), default="draft", index=True)
    answer_mode: Mapped[str] = mapped_column(String(40), default="guided_answer", index=True)
    file_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    metadata_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    search_vector: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    search_tsvector: Mapped[Optional[str]] = mapped_column(Text().with_variant(TSVECTOR(), "postgresql"), nullable=True)
    embedding: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    embedding_vector: Mapped[Optional[str]] = mapped_column(
        Text().with_variant(PGVector(KNOWLEDGE_VECTOR_DIMENSION), "postgresql"),
        nullable=True,
    )
    embedding_model: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    embedding_dim: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    embedding_status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    embedding_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    embedded_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True, index=True)
    lexical_config: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    retrieval_metadata_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    section_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    chunk_type: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    source_document_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    source_page: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    source_row: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    semantic_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)


class KnowledgeItemVersion(Base):
    __tablename__ = "knowledge_item_versions"
    __table_args__ = (UniqueConstraint("item_id", "version", name="uq_knowledge_item_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("knowledge_items.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, index=True)
    snapshot_json: Mapped[dict] = mapped_column(JSON)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    published_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    published_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)


class ChannelOnboardingTask(Base):
    __tablename__ = "channel_onboarding_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    requested_by: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    market_id: Mapped[Optional[int]] = mapped_column(ForeignKey("markets.id"), nullable=True, index=True)
    target_slot: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    desired_display_name: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    desired_channel_account_binding: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    external_channel_account_id: Mapped[Optional[str]] = mapped_column(String(160), nullable=True, index=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)
    started_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
