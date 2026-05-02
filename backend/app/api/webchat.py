from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..settings import get_settings
from ..unit_of_work import managed_session
from .deps import get_current_user
from ..services.webchat_rate_limit import enforce_webchat_rate_limit
from ..services.webchat_service import (
    add_visitor_message,
    admin_get_thread,
    admin_list_conversations,
    admin_reply,
    create_or_resume_conversation,
    list_public_messages,
)

router = APIRouter(prefix="/api/webchat", tags=["webchat"])
settings = get_settings()


class WebchatInitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_key: str = Field(default="default", max_length=120)
    channel_key: str = Field(default="default", max_length=120)
    conversation_id: str | None = Field(default=None, max_length=64)
    visitor_token: str | None = Field(default=None, max_length=160)
    visitor_name: str | None = Field(default=None, max_length=160)
    visitor_email: str | None = Field(default=None, max_length=200)
    visitor_phone: str | None = Field(default=None, max_length=80)
    visitor_ref: str | None = Field(default=None, max_length=160)
    origin: str | None = Field(default=None, max_length=255)
    page_url: str | None = Field(default=None, max_length=700)


class WebchatSendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visitor_token: str | None = Field(default=None, min_length=20, max_length=160)
    body: str = Field(min_length=1, max_length=2000)


class WebchatReplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=1, max_length=2000)
    has_fact_evidence: bool = False
    confirm_review: bool = False
