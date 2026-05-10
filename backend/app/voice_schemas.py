from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class WebchatVoiceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locale: str | None = Field(default=None, max_length=20)
    recording_consent: bool = False


class WebchatVoiceSessionRead(BaseModel):
    ok: bool = True
    voice_session_id: str
    status: str
    provider: str
    voice_page_url: str | None = None
    room_name: str
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


class WebchatVoiceStatusResponse(BaseModel):
    ok: bool = True
    status: str
    voice_session_id: str
    accepted_by_user_id: int | None = None
