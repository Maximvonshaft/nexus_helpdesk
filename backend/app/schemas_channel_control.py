from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from .utils.time import format_utc


class ChannelControlModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    @field_serializer("*", when_used="json", check_fields=False)
    def serialize_common_types(self, value: Any):
        if isinstance(value, datetime):
            return format_utc(value)
        return value


class ChannelOnboardingTaskCreate(BaseModel):
    provider: str = Field(min_length=1, max_length=40)
    market_id: Optional[int] = None
    target_slot: Optional[str] = Field(default=None, max_length=120)
    desired_display_name: Optional[str] = Field(default=None, max_length=160)
    desired_channel_account_binding: Optional[str] = Field(default=None, max_length=160)
    openclaw_account_id: Optional[str] = Field(default=None, max_length=160)

    @field_validator("provider", "target_slot", "desired_display_name", "desired_channel_account_binding", "openclaw_account_id", mode="before")
    @classmethod
    def strip_strings(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


class ChannelOnboardingTaskUpdate(BaseModel):
    market_id: Optional[int] = None
    target_slot: Optional[str] = Field(default=None, max_length=120)
    desired_display_name: Optional[str] = Field(default=None, max_length=160)
    desired_channel_account_binding: Optional[str] = Field(default=None, max_length=160)
    openclaw_account_id: Optional[str] = Field(default=None, max_length=160)

    @field_validator("target_slot", "desired_display_name", "desired_channel_account_binding", "openclaw_account_id", mode="before")
    @classmethod
    def strip_strings(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


class ChannelOnboardingTaskFailRequest(BaseModel):
    last_error: str = Field(min_length=1, max_length=4000)

    @field_validator("last_error", mode="before")
    @classmethod
    def strip_error(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


class ChannelOnboardingTaskCompleteRequest(BaseModel):
    openclaw_account_id: Optional[str] = Field(default=None, max_length=160)
    desired_channel_account_binding: Optional[str] = Field(default=None, max_length=160)

    @field_validator("openclaw_account_id", "desired_channel_account_binding", mode="before")
    @classmethod
    def strip_strings(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


class ChannelOnboardingTaskOut(ChannelControlModel):
    id: int
    provider: str
    status: str
    requested_by: Optional[int] = None
    market_id: Optional[int] = None
    target_slot: Optional[str] = None
    desired_display_name: Optional[str] = None
    desired_channel_account_binding: Optional[str] = None
    openclaw_account_id: Optional[str] = None
    last_error: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class ChannelOnboardingTaskListOut(BaseModel):
    tasks: list[ChannelOnboardingTaskOut]
    total: int
