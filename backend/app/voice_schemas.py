from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WebchatVoiceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locale: str | None = Field(default=None, max_length=20)
    recording_consent: bool = False


class WebchatVoiceRejectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=240)


class WebchatVoiceNoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=4000)
    source: str | None = Field(default=None, max_length=80)

    @field_validator("body", "source", mode="before")
    @classmethod
    def strip_text(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value


class WebchatVoiceNoteResponse(BaseModel):
    ok: bool = True
    ticket_id: int
    voice_session_id: str
    note_id: int
    ticket_event_id: int
    webchat_event_id: int
    audit_id: int
    created_at: str


class WebchatVoiceSessionRead(BaseModel):
    ok: bool = True
    voice_session_id: str
    status: str
    provider: str
    voice_page_url: str | None = None
    room_name: str
    provider_room_name: str | None = None
    participant_identity: str | None = None
    participant_token: str | None = None
    expires_in_seconds: int | None = None
    accepted_by_user_id: int | None = None
    started_at: str | None = None
    ringing_at: str | None = None
    accepted_at: str | None = None
    active_at: str | None = None
    ended_at: str | None = None
    expires_at: str | None = None


class WebchatVoiceSessionList(BaseModel):
    items: list[WebchatVoiceSessionRead]


class WebchatVoiceIncomingSessionRead(WebchatVoiceSessionRead):
    ticket_id: int
    ticket_no: str | None = None
    ticket_title: str | None = None
    conversation_id: str | None = None
    visitor_label: str | None = None
    origin: str | None = None
    page_url: str | None = None


class WebchatVoiceIncomingSessionList(BaseModel):
    items: list[WebchatVoiceIncomingSessionRead]


class WebchatVoiceStatusResponse(BaseModel):
    ok: bool = True
    status: str
    voice_session_id: str
    accepted_by_user_id: int | None = None
