from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from .utils.time import format_utc

MESSAGE_TYPE_ALLOWLIST = {"text", "system", "card", "action", "attachment"}
CARD_TYPE_ALLOWLIST = {
    "quick_replies",
    "tracking_status",
    "address_confirmation",
    "reschedule_picker",
    "photo_upload_request",
    "handoff",
    "csat",
}
ACTION_TYPE_ALLOWLIST = {
    "quick_reply",
    "handoff_request",
    "address_confirm",
    "address_edit",
    "address_cancel",
    "reschedule_submit",
    "photo_upload_submit",
    "csat_submit",
}
SAFE_CARD_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,119}$")
SAFE_ACTION_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,79}$")
HTML_MARKUP_RE = re.compile(r"<\s*/?\s*(script|iframe|style|object|embed|link|meta|html|body|svg|math|[a-zA-Z][a-zA-Z0-9:-]*)\b", re.IGNORECASE)
UNSAFE_TEXT_RE = re.compile(r"javascript:|data:text/html|vbscript:", re.IGNORECASE)
MAX_CARD_PAYLOAD_BYTES = 12_000


def _reject_unsafe_text(value: str | None, *, field_name: str, max_len: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if len(text) > max_len:
        raise ValueError(f"{field_name} exceeds {max_len} characters")
    if HTML_MARKUP_RE.search(text):
        raise ValueError(f"{field_name} must not contain HTML or executable markup")
    if UNSAFE_TEXT_RE.search(text):
        raise ValueError(f"{field_name} contains unsafe content")
    return text


def _validate_urls_are_https(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and key.lower().endswith("url") and isinstance(item, str):
                if item and not item.startswith("https://"):
                    raise ValueError("URL fields in WebChat card payloads must use https://")
            _validate_urls_are_https(item)
    elif isinstance(value, list):
        for item in value:
            _validate_urls_are_https(item)
    elif isinstance(value, str):
        if HTML_MARKUP_RE.search(value) or UNSAFE_TEXT_RE.search(value):
            raise ValueError("WebChat card payload text must not contain HTML or executable markup")


class WebChatAPIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    @field_serializer('*', when_used='json', check_fields=False)
    def serialize_common_types(self, value: Any):
        if isinstance(value, datetime):
            return format_utc(value)
        return value


class WebChatCardAction(BaseModel):
    id: str = Field(max_length=80)
    label: str = Field(max_length=80)
    value: str | None = Field(default=None, max_length=200)
    action_type: str = Field(default="quick_reply", max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def validate_action_id(cls, value: str) -> str:
        if not SAFE_ACTION_ID_RE.match(value or ""):
            raise ValueError("action id must be a safe string")
        return value

    @field_validator("label", "value")
    @classmethod
    def validate_action_text(cls, value: str | None, info):
        return _reject_unsafe_text(value, field_name=info.field_name, max_len=80 if info.field_name == "label" else 200)

    @field_validator("action_type")
    @classmethod
    def validate_action_type(cls, value: str) -> str:
        if value not in ACTION_TYPE_ALLOWLIST:
            raise ValueError("unsupported WebChat card action_type")
        return value

    @model_validator(mode="after")
    def validate_action_payload_security(self):
        _validate_urls_are_https(self.payload)
        return self


class WebChatCardPayload(BaseModel):
    card_id: str = Field(max_length=120)
    card_type: str = Field(max_length=64)
    version: int = Field(default=1, ge=1, le=5)
    title: str = Field(max_length=120)
    body: str | None = Field(default=None, max_length=600)
    actions: list[WebChatCardAction] = Field(default_factory=list, max_length=8)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("card_id")
    @classmethod
    def validate_card_id(cls, value: str) -> str:
        if not SAFE_CARD_ID_RE.match(value or ""):
            raise ValueError("card_id must be a safe string")
        return value

    @field_validator("card_type")
    @classmethod
    def validate_card_type(cls, value: str) -> str:
        if value not in CARD_TYPE_ALLOWLIST:
            raise ValueError("unsupported WebChat card_type")
        return value

    @field_validator("title", "body")
    @classmethod
    def validate_card_text(cls, value: str | None, info):
        return _reject_unsafe_text(value, field_name=info.field_name, max_len=120 if info.field_name == "title" else 600)

    @model_validator(mode="after")
    def validate_payload_security(self):
        encoded_size = len(self.model_dump_json().encode("utf-8"))
        if encoded_size > MAX_CARD_PAYLOAD_BYTES:
            raise ValueError("WebChat card payload is too large")
        _validate_urls_are_https(self.model_dump())
        if self.card_type in {"quick_replies", "handoff"} and not self.actions:
            raise ValueError(f"{self.card_type} card requires at least one action")
        return self


class WebChatMessageRead(BaseModel):
    id: int
    direction: str
    body: str
    body_text: str | None = None
    message_type: str = "text"
    payload_json: dict[str, Any] | None = None
    metadata_json: dict[str, Any] | None = None
    client_message_id: str | None = None
    delivery_status: str = "sent"
    action_status: str | None = None
    author_label: str | None = None
    created_at: str | None = None


class WebChatActionSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visitor_token: str | None = Field(default=None, min_length=20, max_length=160)
    message_id: int
    card_id: str = Field(max_length=120)
    action_id: str = Field(max_length=80)
    action_type: str = Field(default="quick_reply", max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("card_id")
    @classmethod
    def validate_submit_card_id(cls, value: str) -> str:
        if not SAFE_CARD_ID_RE.match(value or ""):
            raise ValueError("unsafe card id")
        return value

    @field_validator("action_id")
    @classmethod
    def validate_submit_action_id(cls, value: str) -> str:
        if not SAFE_ACTION_ID_RE.match(value or ""):
            raise ValueError("unsafe action id")
        return value

    @field_validator("action_type")
    @classmethod
    def validate_submit_action_type(cls, value: str) -> str:
        if value not in ACTION_TYPE_ALLOWLIST:
            raise ValueError("unsupported WebChat action_type")
        return value

    @model_validator(mode="after")
    def validate_submit_payload(self):
        _validate_urls_are_https(self.payload)
        if len(str(self.payload).encode("utf-8")) > 6000:
            raise ValueError("WebChat action payload is too large")
        return self


class WebChatActionSubmitResponse(BaseModel):
    ok: bool
    action_id: int
    status: str
    message: WebChatMessageRead
    handoff_triggered: bool = False


class WebChatIncrementalMessagesResponse(BaseModel):
    conversation_id: str
    status: str
    messages: list[WebChatMessageRead]
    has_more: bool = False
    next_after_id: int | None = None


WebChatCardType = Literal[
    "quick_replies",
    "tracking_status",
    "address_confirmation",
    "reschedule_picker",
    "photo_upload_request",
    "handoff",
    "csat",
]


class WebChatSiteCreate(BaseModel):
    site_key: str
    name: str
    widget_title: Optional[str] = None
    logo_url: Optional[str] = None
    welcome_message: Optional[str] = None
    default_language: Optional[str] = None
    allowed_origins: list[str] = Field(default_factory=list)
    theme_json: Optional[dict[str, Any]] = None
    business_hours_json: Optional[dict[str, Any]] = None
    mapped_market_id: Optional[int] = None
    mapped_team_id: Optional[int] = None
    mapped_openclaw_agent: Optional[str] = None
    is_active: bool = True


class WebChatSiteUpdate(BaseModel):
    name: Optional[str] = None
    widget_title: Optional[str] = None
    logo_url: Optional[str] = None
    welcome_message: Optional[str] = None
    default_language: Optional[str] = None
    allowed_origins: Optional[list[str]] = None
    theme_json: Optional[dict[str, Any]] = None
    business_hours_json: Optional[dict[str, Any]] = None
    mapped_market_id: Optional[int] = None
    mapped_team_id: Optional[int] = None
    mapped_openclaw_agent: Optional[str] = None
    is_active: Optional[bool] = None


class WebChatSiteRead(WebChatAPIModel):
    id: int
    site_key: str
    name: str
    widget_title: Optional[str] = None
    logo_url: Optional[str] = None
    welcome_message: Optional[str] = None
    default_language: Optional[str] = None
    allowed_origins: list[str] = Field(default_factory=list)
    theme_json: Optional[dict[str, Any]] = None
    business_hours_json: Optional[dict[str, Any]] = None
    mapped_market_id: Optional[int] = None
    mapped_team_id: Optional[int] = None
    mapped_openclaw_agent: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class WebChatBootstrapRequest(BaseModel):
    site_id: str
    visitor_id: Optional[str] = None
    browser_session_id: Optional[str] = None
    locale: Optional[str] = None
    timezone: Optional[str] = None
    page_url: Optional[str] = None
    page_title: Optional[str] = None


class WebChatSessionRead(WebChatAPIModel):
    id: int
    site_id: int
    ticket_id: Optional[int] = None
    visitor_id: str
    browser_session_id: str
    status: str
    handoff_status: str
    origin: Optional[str] = None
    locale: Optional[str] = None
    timezone: Optional[str] = None
    last_message_preview: Optional[str] = None
    last_message_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    last_active_at: datetime
    expires_at: Optional[datetime] = None


class WebChatBootstrapResponse(BaseModel):
    site: WebChatSiteRead
    session: WebChatSessionRead
    stream_url: str
    widget_title: str
    welcome_message: str


class WebChatHistoryItem(BaseModel):
    id: str
    role: str
    author_name: Optional[str] = None
    text: Optional[str] = None
    created_at: Optional[str] = None


class WebChatHistoryResponse(BaseModel):
    items: list[WebChatHistoryItem] = Field(default_factory=list)
    has_more: bool = False


class WebChatSendRequest(BaseModel):
    browser_session_id: str
    client_message_id: str
    text: str
    page_url: Optional[str] = None
    page_title: Optional[str] = None


class WebChatSendResponse(BaseModel):
    accepted: bool
    client_message_id: str
    browser_session_id: str
    status: str


class WebChatAbortRequest(BaseModel):
    browser_session_id: str


class WebChatAbortResponse(BaseModel):
    aborted: bool
    supported: bool = False
    detail: Optional[str] = None


class WebChatHandoffCreate(BaseModel):
    browser_session_id: str
    reason: Optional[str] = None
    note: Optional[str] = None


class WebChatHandoffRead(WebChatAPIModel):
    id: int
    session_id: int
    requested_by: str
    status: str
    reason: Optional[str] = None
    note: Optional[str] = None
    assigned_to_user_id: Optional[int] = None
    created_ticket_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime


class WebChatCreateTicketRequest(BaseModel):
    browser_session_id: str
    title: Optional[str] = None
    description: Optional[str] = None
    customer_name: Optional[str] = None
    customer_email: Optional[str] = None


class WebChatTicketUpgradeRead(WebChatAPIModel):
    id: int
    session_id: int
    ticket_id: int
    upgrade_type: str
    created_by_user_id: Optional[int] = None
    created_at: datetime


class WebChatConversationDetail(BaseModel):
    session: WebChatSessionRead
    site: WebChatSiteRead
    items: list[WebChatHistoryItem] = Field(default_factory=list)
    ticket_id: Optional[int] = None
    handoffs: list[WebChatHandoffRead] = Field(default_factory=list)
    upgrades: list[WebChatTicketUpgradeRead] = Field(default_factory=list)
