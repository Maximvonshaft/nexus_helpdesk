from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from .utils.time import format_utc

PROFILE_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,119}$")


JsonObject = dict[str, Any]


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

    @field_validator("name", "description", "channel", "language", "draft_summary", mode="before")
    @classmethod
    def strip_optional_strings(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


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

    @field_validator("name", "description", "channel", "language", "draft_summary", mode="before")
    @classmethod
    def strip_optional_strings(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


class PersonaPublishRequest(BaseModel):
    notes: Optional[str] = Field(default=None, max_length=4000)

    @field_validator("notes", mode="before")
    @classmethod
    def strip_notes(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


class PersonaRollbackRequest(PersonaPublishRequest):
    version: int = Field(gt=0)


class PersonaResolvePreviewRequest(BaseModel):
    market_id: Optional[int] = None
    channel: Optional[str] = Field(default=None, max_length=40)
    language: Optional[str] = Field(default=None, max_length=16)

    @field_validator("channel", "language", mode="before")
    @classmethod
    def strip_optional_strings(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


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
    market_id: Optional[int] = None
    channel: Optional[str] = Field(default=None, max_length=40)
    audience_scope: str = Field(default="customer", max_length=40)
    priority: int = Field(default=100, ge=0, le=10000)
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    source_url: Optional[str] = Field(default=None, max_length=500)
    file_name: Optional[str] = Field(default=None, max_length=255)
    file_storage_key: Optional[str] = Field(default=None, max_length=255)
    mime_type: Optional[str] = Field(default=None, max_length=120)
    file_size: Optional[int] = Field(default=None, ge=0)
    draft_body: Optional[str] = Field(default=None, max_length=120000)
    draft_normalized_text: Optional[str] = Field(default=None, max_length=120000)

    @field_validator("title", "summary", "status", "source_type", "channel", "audience_scope", "source_url", "file_name", "file_storage_key", "mime_type", "draft_body", "draft_normalized_text", mode="before")
    @classmethod
    def strip_optional_strings(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


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
    market_id: Optional[int] = None
    channel: Optional[str] = Field(default=None, max_length=40)
    audience_scope: Optional[str] = Field(default=None, max_length=40)
    priority: Optional[int] = Field(default=None, ge=0, le=10000)
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    source_url: Optional[str] = Field(default=None, max_length=500)
    file_name: Optional[str] = Field(default=None, max_length=255)
    file_storage_key: Optional[str] = Field(default=None, max_length=255)
    mime_type: Optional[str] = Field(default=None, max_length=120)
    file_size: Optional[int] = Field(default=None, ge=0)
    draft_body: Optional[str] = Field(default=None, max_length=120000)
    draft_normalized_text: Optional[str] = Field(default=None, max_length=120000)

    @field_validator("title", "summary", "status", "source_type", "channel", "audience_scope", "source_url", "file_name", "file_storage_key", "mime_type", "draft_body", "draft_normalized_text", mode="before")
    @classmethod
    def strip_optional_strings(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


class KnowledgePublishRequest(BaseModel):
    notes: Optional[str] = Field(default=None, max_length=4000)

    @field_validator("notes", mode="before")
    @classmethod
    def strip_notes(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


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
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


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
    market_id: Optional[int] = None
    channel: Optional[str] = None
    audience_scope: str
    priority: int
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    source_url: Optional[str] = None
    file_name: Optional[str] = None
    file_storage_key: Optional[str] = None
    mime_type: Optional[str] = None
    file_size: Optional[int] = None
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
