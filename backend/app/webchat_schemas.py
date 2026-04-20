from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from .utils.time import format_utc


class WebChatAPIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    @field_serializer('*', when_used='json', check_fields=False)
    def serialize_common_types(self, value: Any):
        if isinstance(value, datetime):
            return format_utc(value)
        return value


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
