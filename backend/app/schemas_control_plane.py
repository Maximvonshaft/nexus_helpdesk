from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from .utils.time import format_utc

PROFILE_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,119}$")
GLOBAL_SCOPE_ALIASES = {"*", "all", "any", "global", "none", "null"}


JsonObject = dict[str, Any]


def _strip_optional_string(value):
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _strip_optional_language(value):
    cleaned = _strip_optional_string(value)
    if isinstance(cleaned, str) and cleaned.lower() in GLOBAL_SCOPE_ALIASES:
        return None
    return cleaned


class ControlPlaneModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    @field_serializer("*", when_used="json", check_fields=False)
    def serialize_common_types(self, value: Any):
        if isinstance(value, datetime):
            return format_utc(value)
        return value


class PersonaProfileBase(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: Optional[str] = Field(default=None, max_length=4000)
    market_id: Optional[int] = None
    channel: Optional[str] = Field(default=None, max_length=40)
    language: Optional[str] = Field(default=None, max_length=16)
    is_active: bool = True
    draft_summary: Optional[str] = Field(default=None, max_length=8000)
    draft_content_json: Optional[JsonObject] = None

    @field_validator("name", "description", "channel", "draft_summary", mode="before")
    @classmethod
    def strip_optional_strings(cls, value):
        return _strip_optional_string(value)

    @field_validator("language", mode="before")
    @classmethod
    def strip_optional_language(cls, value):
        return _strip_optional_language(value)


class PersonaProfileCreate(PersonaProfileBase):
    profile_key: str = Field(min_length=2, max_length=120)

    @field_validator("profile_key")
    @classmethod
    def validate_profile_key(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not PROFILE_KEY_RE.match(cleaned):
            raise ValueError("profile_key must match [a-z0-9][a-z0-9_.-]{1,119}")
        return cleaned


class PersonaProfileUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=160)
    description: Optional[str] = Field(default=None, max_length=4000)
    market_id: Optional[int] = None
    channel: Optional[str] = Field(default=None, max_length=40)
    language: Optional[str] = Field(default=None, max_length=16)
    is_active: Optional[bool] = None
    draft_summary: Optional[str] = Field(default=None, max_length=8000)
    draft_content_json: Optional[JsonObject] = None

    @field_validator("name", "description", "channel", "draft_summary", mode="before")
    @classmethod
    def strip_optional_strings(cls, value):
        return _strip_optional_string(value)

    @field_validator("language", mode="before")
    @classmethod
    def strip_optional_language(cls, value):
        return _strip_optional_language(value)


class PersonaPublishRequest(BaseModel):
    notes: Optional[str] = Field(default=None, max_length=4000)

    @field_validator("notes", mode="before")
    @classmethod
    def strip_notes(cls, value):
        return _strip_optional_string(value)


class PersonaRollbackRequest(PersonaPublishRequest):
    version: int = Field(gt=0)


class PersonaResolvePreviewRequest(BaseModel):
    market_id: Optional[int] = None
    channel: Optional[str] = Field(default=None, max_length=40)
    language: Optional[str] = Field(default=None, max_length=16)

    @field_validator("channel", mode="before")
    @classmethod
    def strip_optional_strings(cls, value):
        return _strip_optional_string(value)

    @field_validator("language", mode="before")
    @classmethod
    def strip_optional_language(cls, value):
        return _strip_optional_language(value)


class PersonaProfileVersionOut(ControlPlaneModel):
    id: int
    profile_id: int
    version: int
    snapshot_json: JsonObject
    summary: Optional[str] = None
    notes: Optional[str] = None
    published_by: Optional[int] = None
    published_at: datetime


class PersonaProfileOut(ControlPlaneModel):
    id: int
    profile_key: str
    name: str
    description: Optional[str] = None
    market_id: Optional[int] = None
    channel: Optional[str] = None
    language: Optional[str] = None
    is_active: bool
    draft_summary: Optional[str] = None
    draft_content_json: Optional[JsonObject] = None
    published_summary: Optional[str] = None
    published_content_json: Optional[JsonObject] = None
    published_version: int
    published_at: Optional[datetime] = None
    created_by: Optional[int] = None
    updated_by: Optional[int] = None
    published_by: Optional[int] = None
    created_at: datetime
    updated_at: datetime


class PersonaProfileDetailOut(PersonaProfileOut):
    versions: list[PersonaProfileVersionOut] = Field(default_factory=list)


class PersonaProfileListOut(BaseModel):
    profiles: list[PersonaProfileOut]
    total: int


class PersonaResolvePreviewOut(BaseModel):
    profile: Optional[PersonaProfileOut] = None
    match_rank: Optional[int] = None


class KnowledgeItemBase(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    summary: Optional[str] = Field(default=None, max_length=4000)
    status: str = Field(default="draft", max_length=40)
    source_type: str = Field(default="text", max_length=20)
    knowledge_kind: str = Field(default="document", max_length=40)
    market_id: Optional[int] = None
    channel: Optional[str] = Field(default=None, max_length=40)
    audience_scope: str = Field(default="customer", max_length=40)
    language: Optional[str] = Field(default=None, max_length=16)
    priority: int = Field(default=100, ge=0, le=10000)
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    source_url: Optional[str] = Field(default=None, max_length=500)
    file_name: Optional[str] = Field(default=None, max_length=255)
    file_storage_key: Optional[str] = Field(default=None, max_length=255)
    mime_type: Optional[str] = Field(default=None, max_length=120)
    file_size: Optional[int] = Field(default=None, ge=0)
    fact_question: Optional[str] = Field(default=None, max_length=20000)
    fact_answer: Optional[str] = Field(default=None, max_length=40000)
    fact_aliases_json: Optional[list[str]] = Field(default=None, max_length=50)
    fact_status: str = Field(default="draft", max_length=40)
    answer_mode: str = Field(default="guided_answer", max_length=40)
    citation_metadata_json: Optional[JsonObject] = None
    draft_body: Optional[str] = Field(default=None, max_length=120000)
    draft_normalized_text: Optional[str] = Field(default=None, max_length=120000)

    @field_validator("title", "summary", "status", "source_type", "knowledge_kind", "channel", "audience_scope", "language", "source_url", "file_name", "file_storage_key", "mime_type", "fact_question", "fact_answer", "fact_status", "answer_mode", "draft_body", "draft_normalized_text", mode="before")
    @classmethod
    def strip_optional_strings(cls, value):
        return _strip_optional_string(value)


class KnowledgeItemCreate(KnowledgeItemBase):
    item_key: str = Field(min_length=2, max_length=120)

    @field_validator("item_key")
    @classmethod
    def validate_item_key(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not PROFILE_KEY_RE.match(cleaned):
            raise ValueError("item_key must match [a-z0-9][a-z0-9_.-]{1,119}")
        return cleaned


class KnowledgeItemUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    summary: Optional[str] = Field(default=None, max_length=4000)
    status: Optional[str] = Field(default=None, max_length=40)
    source_type: Optional[str] = Field(default=None, max_length=20)
    knowledge_kind: Optional[str] = Field(default=None, max_length=40)
    market_id: Optional[int] = None
    channel: Optional[str] = Field(default=None, max_length=40)
    audience_scope: Optional[str] = Field(default=None, max_length=40)
    language: Optional[str] = Field(default=None, max_length=16)
    priority: Optional[int] = Field(default=None, ge=0, le=10000)
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    source_url: Optional[str] = Field(default=None, max_length=500)
    file_name: Optional[str] = Field(default=None, max_length=255)
    file_storage_key: Optional[str] = Field(default=None, max_length=255)
    mime_type: Optional[str] = Field(default=None, max_length=120)
    file_size: Optional[int] = Field(default=None, ge=0)
    fact_question: Optional[str] = Field(default=None, max_length=20000)
    fact_answer: Optional[str] = Field(default=None, max_length=40000)
    fact_aliases_json: Optional[list[str]] = Field(default=None, max_length=50)
    fact_status: Optional[str] = Field(default=None, max_length=40)
    answer_mode: Optional[str] = Field(default=None, max_length=40)
    citation_metadata_json: Optional[JsonObject] = None
    draft_body: Optional[str] = Field(default=None, max_length=120000)
    draft_normalized_text: Optional[str] = Field(default=None, max_length=120000)

    @field_validator("title", "summary", "status", "source_type", "knowledge_kind", "channel", "audience_scope", "language", "source_url", "file_name", "file_storage_key", "mime_type", "fact_question", "fact_answer", "fact_status", "answer_mode", "draft_body", "draft_normalized_text", mode="before")
    @classmethod
    def strip_optional_strings(cls, value):
        return _strip_optional_string(value)


class KnowledgePublishRequest(BaseModel):
    notes: Optional[str] = Field(default=None, max_length=4000)

    @field_validator("notes", mode="before")
    @classmethod
    def strip_notes(cls, value):
        return _strip_optional_string(value)


class KnowledgeRollbackRequest(KnowledgePublishRequest):
    version: int = Field(gt=0)


class KnowledgeSearchPublishedRequest(BaseModel):
    q: Optional[str] = Field(default=None, max_length=200)
    market_id: Optional[int] = None
    channel: Optional[str] = Field(default=None, max_length=40)
    audience_scope: Optional[str] = Field(default="customer", max_length=40)
    limit: int = Field(default=20, ge=1, le=100)

    @field_validator("q", "channel", "audience_scope", mode="before")
    @classmethod
    def strip_optional_strings(cls, value):
        return _strip_optional_string(value)


class KnowledgeRetrievalTestRequest(BaseModel):
    q: str = Field(min_length=1, max_length=500)
    market_id: Optional[int] = None
    channel: Optional[str] = Field(default=None, max_length=40)
    audience_scope: Optional[str] = Field(default="customer", max_length=40)
    language: Optional[str] = Field(default=None, max_length=16)
    limit: int = Field(default=5, ge=1, le=20)

    @field_validator("q", "channel", "audience_scope", "language", mode="before")
    @classmethod
    def strip_optional_strings(cls, value):
        return _strip_optional_string(value)


class KnowledgeRuntimeContextTestRequest(KnowledgeRetrievalTestRequest):
    tenant_key: str = Field(default="default", min_length=1, max_length=120)


class KnowledgeItemVersionOut(ControlPlaneModel):
    id: int
    item_id: int
    version: int
    snapshot_json: JsonObject
    summary: Optional[str] = None
    notes: Optional[str] = None
    published_by: Optional[int] = None
    published_at: datetime


class KnowledgeItemOut(ControlPlaneModel):
    id: int
    item_key: str
    title: str
    summary: Optional[str] = None
    status: str
    source_type: str
    knowledge_kind: str = "document"
    market_id: Optional[int] = None
    channel: Optional[str] = None
    audience_scope: str
    language: Optional[str] = None
    priority: int
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    source_url: Optional[str] = None
    file_name: Optional[str] = None
    file_storage_key: Optional[str] = None
    mime_type: Optional[str] = None
    file_size: Optional[int] = None
    parsing_status: Optional[str] = None
    parsing_error: Optional[str] = None
    parsed_at: Optional[datetime] = None
    indexed_version: int = 0
    indexed_at: Optional[datetime] = None
    chunk_count: int = 0
    fact_question: Optional[str] = None
    fact_answer: Optional[str] = None
    fact_aliases_json: Optional[list[str]] = None
    fact_status: str = "draft"
    answer_mode: str = "guided_answer"
    citation_metadata_json: Optional[JsonObject] = None
    draft_body: Optional[str] = None
    draft_normalized_text: Optional[str] = None
    published_body: Optional[str] = None
    published_normalized_text: Optional[str] = None
    published_version: int
    published_at: Optional[datetime] = None
    created_by: Optional[int] = None
    updated_by: Optional[int] = None
    published_by: Optional[int] = None
    created_at: datetime
    updated_at: datetime


class KnowledgeItemDetailOut(KnowledgeItemOut):
    versions: list[KnowledgeItemVersionOut] = Field(default_factory=list)


class KnowledgeItemListOut(BaseModel):
    items: list[KnowledgeItemOut]
    total: int


class KnowledgeSearchPublishedOut(BaseModel):
    items: list[KnowledgeItemOut]
    total: int


class KnowledgeChunkHitOut(BaseModel):
    item_id: int
    item_key: str
    title: str
    published_version: int
    chunk_index: int
    score: float
    text: str
    retrieval_method: str | None = None
    matched_terms: list[str] = Field(default_factory=list)
    score_breakdown: JsonObject = Field(default_factory=dict)
    direct_answer: Optional[str] = None
    answer_mode: Optional[str] = None
    source_metadata: JsonObject = Field(default_factory=dict)
    metadata: JsonObject = Field(default_factory=dict)


class KnowledgeQueryAnalysisOut(BaseModel):
    language: str
    normalized_query: str
    entity_terms: list[str] = Field(default_factory=list)
    service_terms: list[str] = Field(default_factory=list)
    numeric_terms: list[str] = Field(default_factory=list)
    intent_terms: list[str] = Field(default_factory=list)
    terms: list[str] = Field(default_factory=list)
    high_value_terms: list[str] = Field(default_factory=list)
    fallback_ngrams: list[str] = Field(default_factory=list)


class KnowledgeRetrievalTestOut(BaseModel):
    hits: list[KnowledgeChunkHitOut]
    total: int
    query_analysis: KnowledgeQueryAnalysisOut | None = None
    candidate_count: int = 0
    top_hits: list[JsonObject] = Field(default_factory=list)
    grounding_would_apply: bool = False
    grounding_source: Optional[JsonObject] = None


class KnowledgeRuntimeContextTestOut(BaseModel):
    context: JsonObject
