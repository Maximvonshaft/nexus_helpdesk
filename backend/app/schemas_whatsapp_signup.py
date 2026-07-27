from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EmbeddedSignupSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmbeddedSignupSessionCreate(EmbeddedSignupSchema):
    display_name: str = Field(min_length=1, max_length=160)
    account_id: str = Field(min_length=1, max_length=160)
    market_id: int | None = None
    priority: int = Field(default=100, ge=0, le=10000)

    @field_validator("display_name", "account_id", mode="before")
    @classmethod
    def strip_required(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value


class EmbeddedSignupSessionRead(EmbeddedSignupSchema):
    session_id: str
    state: str
    expires_at: datetime
    app_id: str
    configuration_id: str
    graph_api_version: str
    allowed_origin: str


class EmbeddedSignupCompleteRequest(EmbeddedSignupSchema):
    state: str = Field(min_length=32, max_length=512)
    code: str = Field(min_length=8, max_length=8192)
    business_account_id: str | None = Field(default=None, max_length=120)
    waba_id: str = Field(min_length=1, max_length=120)
    phone_number_id: str = Field(min_length=1, max_length=120)
    display_name: str = Field(min_length=1, max_length=160)
    account_id: str = Field(min_length=1, max_length=160)
    market_id: int | None = None
    priority: int = Field(default=100, ge=0, le=10000)

    @field_validator(
        "state",
        "code",
        "business_account_id",
        "waba_id",
        "phone_number_id",
        "display_name",
        "account_id",
        mode="before",
    )
    @classmethod
    def strip_strings(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


class EmbeddedSignupCompleteRead(EmbeddedSignupSchema):
    ok: bool
    session_id: str
    connection_id: int
    account_id: str
    waba_id: str
    phone_number_id: str
    desired_state: str
    verification_state: str
