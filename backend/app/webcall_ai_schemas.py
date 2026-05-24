from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class WebCallAISessionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_key: str = Field(default="default", max_length=120)
    visitor_name: str | None = Field(default=None, max_length=160)
    visitor_email: str | None = Field(default=None, max_length=200)
    visitor_phone: str | None = Field(default=None, max_length=80)
    visitor_ref: str | None = Field(default=None, max_length=160)
    page_url: str | None = Field(default=None, max_length=700)
    locale: str | None = Field(default=None, max_length=20)


class WebCallAIEndRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visitor_token: str | None = Field(default=None, min_length=20, max_length=160)


class WebCallAIHandoffRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visitor_token: str | None = Field(default=None, min_length=20, max_length=160)
    reason: str | None = Field(default=None, max_length=240)


class WebCallAITrackingFallbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visitor_token: str | None = Field(default=None, min_length=20, max_length=160)
    tracking_number: str = Field(min_length=4, max_length=80)
