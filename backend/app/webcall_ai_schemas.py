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


class WebCallAIClientAudioTelemetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visitor_token: str | None = Field(default=None, min_length=20, max_length=160)
    stage: str = Field(max_length=80)
    status: str = Field(max_length=40)
    selected_audio_input_label: str | None = Field(default=None, max_length=160)
    selected_audio_input_device_id_hash: str | None = Field(default=None, max_length=80)
    local_track_ready_state: str | None = Field(default=None, max_length=40)
    local_track_enabled: bool | None = None
    local_track_muted: bool | None = None
    livekit_track_sid: str | None = Field(default=None, max_length=160)
    error_name: str | None = Field(default=None, max_length=120)
    error_message: str | None = Field(default=None, max_length=240)
