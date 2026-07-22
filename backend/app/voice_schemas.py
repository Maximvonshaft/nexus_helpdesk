from __future__ import annotations

from typing import Literal

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
    ticket_id: int | None = None
    voice_session_id: str
    note_id: int
    ticket_event_id: int | None = None
    webchat_event_id: int
    audit_id: int
    created_at: str


class WebchatVoiceActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: Literal["hold", "resume", "mute", "unmute", "keypad", "transfer", "add_participant"]
    target: str | None = Field(default=None, max_length=240)
    digits: str | None = Field(default=None, max_length=64, pattern=r"^[0-9*#]+$")
    note: str | None = Field(default=None, max_length=500)
    idempotency_key: str | None = Field(default=None, max_length=160)

    @field_validator("target", "digits", "note", "idempotency_key", mode="before")
    @classmethod
    def strip_action_text(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value


class WebchatVoiceActionRead(BaseModel):
    id: int
    action_type: str
    status: str
    provider_status: str
    provider_reason: str
    idempotency_key: str | None = None
    attempt_count: int = 0
    payload: dict = Field(default_factory=dict)
    actor_user_id: int
    ticket_event_id: int | None = None
    webchat_event_id: int | None = None
    audit_id: int | None = None
    created_at: str | None = None


class WebchatVoiceActionResponse(BaseModel):
    ok: bool = True
    ticket_id: int | None = None
    voice_session_id: str
    action: WebchatVoiceActionRead


class WebchatVoiceActionList(BaseModel):
    items: list[WebchatVoiceActionRead] = Field(default_factory=list)


class WebchatVoiceTranscriptSegmentRead(BaseModel):
    id: int
    segment_id: str
    speaker_type: str
    speaker_label: str | None = None
    language: str | None = None
    is_final: bool
    start_ms: int | None = None
    end_ms: int | None = None
    text: str
    confidence: int | None = None
    redaction_status: str
    created_at: str | None = None


class WebchatVoiceAITurnRead(BaseModel):
    id: int
    turn_index: int
    customer_text_redacted: str | None = None
    ai_response_text_redacted: str | None = None
    language: str | None = None
    intent: str | None = None
    action: str | None = None
    handoff_required: bool
    handoff_reason: str | None = None
    confidence: int | None = None
    provider: str | None = None
    stt_provider: str | None = None
    tts_provider: str | None = None
    latency_ms: int | None = None
    created_at: str | None = None


class WebchatVoiceAIActionRead(BaseModel):
    id: int
    turn_id: int | None = None
    model_action: str
    nexus_decision: str
    decision_reason: str | None = None
    speedaf_tool_name: str | None = None
    background_job_id: int | None = None
    tool_call_log_id: int | None = None
    result_status: str | None = None
    created_at: str | None = None


class WebchatVoiceEvidenceResponse(BaseModel):
    ok: bool = True
    ticket_id: int | None = None
    voice_session_id: str
    status: str
    provider: str
    recording_status: str | None = None
    transcript_status: str | None = None
    summary_status: str | None = None
    ai_agent_status: str | None = None
    ai_turn_count: int = 0
    transcript_segments: list[WebchatVoiceTranscriptSegmentRead] = Field(default_factory=list)
    ai_turns: list[WebchatVoiceAITurnRead] = Field(default_factory=list)
    ai_actions: list[WebchatVoiceAIActionRead] = Field(default_factory=list)


class SpeedafVoiceCallbackActionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    waybillCode: str = Field(min_length=1, max_length=64)
    action: str = Field(min_length=1, max_length=32)
    actionTime: str | None = Field(default=None, max_length=19)
    aiActionSummary: str = Field(min_length=1, max_length=200)
    actionStatus: Literal["SUCCESS", "FAILED"] = "SUCCESS"
    errorCode: str = Field(default="", max_length=80)

    @field_validator("waybillCode", "action", "actionTime", "aiActionSummary", "errorCode", mode="before")
    @classmethod
    def strip_speedaf_voice_callback_text(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value


class SpeedafVoiceCallbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    callSessionId: str | None = Field(default=None, max_length=64)
    isTransferredToHuman: bool = False
    action: SpeedafVoiceCallbackActionPayload

    @field_validator("callSessionId", mode="before")
    @classmethod
    def strip_call_session_id(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value


class SpeedafVoiceCallbackResponse(BaseModel):
    ok: bool = True
    ticket_id: int | None = None
    voice_session_id: str
    status: str
    message: str
    jobId: int
    dedupeKey: str
    ai_action_id: int | None = None


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
    ticket_id: int | None = None
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
