from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from .utils.time import format_utc


JsonObject = dict[str, Any]

SOURCE_TYPES = {"persona", "knowledge", "ai_config", "bulletin", "channel_account", "outbound_email", "speedaf_action"}
RELEASE_TYPES = {"change", "new", "publish", "rollback", "emergency", "config_change"}
RISK_LEVELS = {"low", "medium", "high", "critical"}
RELEASE_STATUSES = {"draft", "pending_review", "approved", "published", "rolled_back", "rejected"}


def _strip_optional(value):
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _strip_required(value):
    if isinstance(value, str):
        return value.strip()
    return value


class GovernanceReleaseModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    @field_serializer("*", when_used="json", check_fields=False)
    def serialize_common_types(self, value: Any):
        if isinstance(value, datetime):
            return format_utc(value)
        return value


class GovernanceReleaseCreate(BaseModel):
    source_type: str = Field(min_length=2, max_length=40)
    source_id: Optional[int] = Field(default=None, ge=1)
    title: str = Field(min_length=3, max_length=200)
    summary: str = Field(min_length=3, max_length=20000)
    release_type: str = Field(default="change", max_length=40)
    status: str = Field(default="pending_review", max_length=40)
    risk_level: str = Field(default="medium", max_length=40)
    impact_json: Optional[JsonObject] = None
    diff_json: Optional[JsonObject] = None
    rollback_plan: Optional[str] = Field(default=None, max_length=20000)
    audit_target_type: Optional[str] = Field(default=None, max_length=80)
    audit_target_id: Optional[int] = Field(default=None, ge=1)

    @field_validator("source_type", "release_type", "status", "risk_level", mode="before")
    @classmethod
    def normalize_enums(cls, value):
        value = _strip_required(value)
        return value.lower() if isinstance(value, str) else value

    @field_validator("title", "summary", mode="before")
    @classmethod
    def strip_required_strings(cls, value):
        return _strip_required(value)

    @field_validator("rollback_plan", "audit_target_type", mode="before")
    @classmethod
    def strip_optional_strings(cls, value):
        return _strip_optional(value)

    @field_validator("source_type")
    @classmethod
    def validate_source_type(cls, value: str) -> str:
        if value not in SOURCE_TYPES:
            raise ValueError(f"source_type must be one of {sorted(SOURCE_TYPES)}")
        return value

    @field_validator("release_type")
    @classmethod
    def validate_release_type(cls, value: str) -> str:
        if value not in RELEASE_TYPES:
            raise ValueError(f"release_type must be one of {sorted(RELEASE_TYPES)}")
        return value

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        if value not in {"draft", "pending_review"}:
            raise ValueError("new release requests can only start as draft or pending_review")
        return value

    @field_validator("risk_level")
    @classmethod
    def validate_risk_level(cls, value: str) -> str:
        if value not in RISK_LEVELS:
            raise ValueError(f"risk_level must be one of {sorted(RISK_LEVELS)}")
        return value


class GovernanceReleaseAction(BaseModel):
    note: Optional[str] = Field(default=None, max_length=4000)

    @field_validator("note", mode="before")
    @classmethod
    def strip_note(cls, value):
        return _strip_optional(value)


class GovernanceReleaseEventRead(GovernanceReleaseModel):
    id: int
    release_id: int
    actor_id: Optional[int] = None
    event_type: str
    note: Optional[str] = None
    payload_json: Optional[JsonObject] = None
    request_id: Optional[str] = None
    created_at: datetime


class GovernanceReleaseRead(GovernanceReleaseModel):
    id: int
    source_type: str
    source_id: Optional[int] = None
    title: str
    summary: str
    release_type: str
    status: str
    risk_level: str
    impact_json: Optional[JsonObject] = None
    diff_json: Optional[JsonObject] = None
    rollback_plan: Optional[str] = None
    audit_target_type: Optional[str] = None
    audit_target_id: Optional[int] = None
    requested_by: Optional[int] = None
    approved_by: Optional[int] = None
    published_by: Optional[int] = None
    rolled_back_by: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    submitted_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    published_at: Optional[datetime] = None
    rolled_back_at: Optional[datetime] = None
    events: list[GovernanceReleaseEventRead] = Field(default_factory=list)


class GovernanceReleaseListRead(BaseModel):
    items: list[GovernanceReleaseRead]
    total: int
    status_counts: dict[str, int]
    source_counts: dict[str, int]
    risk_counts: dict[str, int]
