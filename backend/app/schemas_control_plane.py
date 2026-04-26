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
