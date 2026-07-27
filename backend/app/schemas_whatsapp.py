from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from .utils.time import format_utc


WhatsAppTransport = Literal["baileys_sidecar", "meta_cloud_api"]
WhatsAppDesiredState = Literal["disabled", "active"]


class WhatsAppSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    @field_serializer("*", when_used="json", check_fields=False)
    def serialize_common_types(self, value: Any):
        if isinstance(value, datetime):
            return format_utc(value)
        return value


class WhatsAppConnectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=160)
    account_id: str = Field(min_length=1, max_length=160)
    market_id: int | None = None
    priority: int = Field(default=100, ge=0, le=10000)
    transport: WhatsAppTransport
    sidecar_session_key: str | None = Field(default=None, min_length=1, max_length=160)
    business_account_id: str | None = Field(default=None, max_length=120)
    waba_id: str | None = Field(default=None, max_length=120)
    phone_number_id: str | None = Field(default=None, max_length=120)
    graph_api_version: str | None = Field(default="v23.0", max_length=24)
    access_token: str | None = Field(default=None, min_length=1, max_length=4096)
    app_secret: str | None = Field(default=None, min_length=1, max_length=4096)
    verify_token: str | None = Field(default=None, min_length=16, max_length=512)

    @field_validator(
        "display_name",
        "account_id",
        "sidecar_session_key",
        "business_account_id",
        "waba_id",
        "phone_number_id",
        "graph_api_version",
        "access_token",
        "app_secret",
        "verify_token",
        mode="before",
    )
    @classmethod
    def strip_strings(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @model_validator(mode="after")
    def validate_transport_configuration(self):
        if self.transport == "baileys_sidecar":
            self.sidecar_session_key = self.sidecar_session_key or self.account_id
            return self
        missing = [
            name
            for name, value in (
                ("waba_id", self.waba_id),
                ("phone_number_id", self.phone_number_id),
                ("access_token", self.access_token),
                ("app_secret", self.app_secret),
                ("verify_token", self.verify_token),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                "meta_cloud_configuration_missing:" + ",".join(missing)
            )
        return self


class WhatsAppConnectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    market_id: int | None = None
    priority: int | None = Field(default=None, ge=0, le=10000)
    sidecar_session_key: str | None = Field(default=None, min_length=1, max_length=160)
    business_account_id: str | None = Field(default=None, max_length=120)
    waba_id: str | None = Field(default=None, max_length=120)
    phone_number_id: str | None = Field(default=None, max_length=120)
    graph_api_version: str | None = Field(default=None, max_length=24)
    access_token: str | None = Field(default=None, min_length=1, max_length=4096)
    app_secret: str | None = Field(default=None, min_length=1, max_length=4096)
    verify_token: str | None = Field(default=None, min_length=16, max_length=512)

    @field_validator(
        "display_name",
        "sidecar_session_key",
        "business_account_id",
        "waba_id",
        "phone_number_id",
        "graph_api_version",
        "access_token",
        "app_secret",
        "verify_token",
        mode="before",
    )
    @classmethod
    def strip_strings(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


class WhatsAppDesiredStateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    desired_state: WhatsAppDesiredState


class WhatsAppPairingCodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone_number: str = Field(min_length=8, max_length=32)


class WhatsAppPairingCodeRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pairing_code: str
    phone_number_suffix: str
    expires_at: str | None = None


class WhatsAppMetaSubscriptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    callback_url: str | None = Field(default=None, max_length=2048)

    @field_validator("callback_url", mode="before")
    @classmethod
    def strip_callback(cls, value):
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value


class WhatsAppTestInboundRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_message_id: str = Field(min_length=1, max_length=255)


class WhatsAppTestOutboundRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=8, max_length=180)
    body: str = Field(min_length=1, max_length=4000)


class WhatsAppTestResult(WhatsAppSchema):
    ok: bool
    connection_id: int
    transport: WhatsAppTransport
    provider_message_id: str | None = None
    verification_state: str
    occurred_at: datetime


class WhatsAppBindingStatus(WhatsAppSchema):
    connection_id: int
    channel_account_id: int
    transport: WhatsAppTransport
    observed_state: str
    authentication_state: str
    listener_state: str
    verification_state: str
    desired_generation: int
    observed_generation: int
    qr_status: str | None = None
    qr_data_url: str | None = None
    qr_expires_at: datetime | None = None
    phone_number_mask: str | None = None
    last_connected_at: datetime | None = None
    last_disconnected_at: datetime | None = None
    last_inbound_at: datetime | None = None
    last_outbound_at: datetime | None = None
    last_probe_at: datetime | None = None
    reconnect_count: int
    last_error_code: str | None = None
    last_error_message: str | None = None


class WhatsAppConnectionRead(WhatsAppSchema):
    id: int
    tenant_id: int
    channel_account_id: int
    account_id: str
    display_name: str | None = None
    market_id: int | None = None
    priority: int
    channel_active: bool
    transport: WhatsAppTransport
    desired_state: str
    observed_state: str
    authentication_state: str
    listener_state: str
    verification_state: str
    desired_generation: int
    observed_generation: int
    phone_number_mask: str | None = None
    business_account_id: str | None = None
    waba_id: str | None = None
    phone_number_id: str | None = None
    graph_api_version: str | None = None
    sidecar_session_key: str | None = None
    session_generation: int
    access_token_configured: bool
    app_secret_configured: bool
    verify_token_configured: bool
    last_qr_generated_at: datetime | None = None
    qr_expires_at: datetime | None = None
    last_connected_at: datetime | None = None
    last_disconnected_at: datetime | None = None
    last_inbound_at: datetime | None = None
    last_outbound_at: datetime | None = None
    last_probe_at: datetime | None = None
    last_probe_status: str | None = None
    reconnect_count: int
    last_error_code: str | None = None
    last_error_message: str | None = None
    inbound_tested_at: datetime | None = None
    outbound_tested_at: datetime | None = None
    verified_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
