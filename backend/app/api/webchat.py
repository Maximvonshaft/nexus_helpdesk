from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..unit_of_work import managed_session
from .deps import get_current_user
from ..services.webchat_service import (
    add_visitor_message,
    admin_get_thread,
    admin_list_conversations,
    admin_reply,
    create_or_resume_conversation,
    list_public_messages,
)

router = APIRouter(prefix="/api/webchat", tags=["webchat"])


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
    visitor_token: str = Field(min_length=20, max_length=160)
    body: str = Field(min_length=1, max_length=2000)


class WebchatReplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=1, max_length=2000)
    has_fact_evidence: bool = False
    confirm_review: bool = False


def _public_cors_headers(request: Request) -> dict[str, str]:
    origin = request.headers.get("origin") or "*"
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, X-Requested-With",
        "Access-Control-Max-Age": "600",
        "Vary": "Origin",
        "Cache-Control": "no-store",
    }


def _set_public_cors(response: Response, request: Request) -> None:
    for key, value in _public_cors_headers(request).items():
        response.headers.setdefault(key, value)


@router.options("/{full_path:path}")
def webchat_options(full_path: str, request: Request):
    return Response(status_code=204, headers=_public_cors_headers(request))


@router.post("/init")
def init_webchat(payload: WebchatInitRequest, request: Request, response: Response, db: Session = Depends(get_db)) -> dict[str, Any]:
    _set_public_cors(response, request)
    with managed_session(db):
        result = create_or_resume_conversation(db, payload, request)
    return result


@router.post("/conversations/{conversation_id}/messages")
def send_webchat_message(conversation_id: str, payload: WebchatSendRequest, request: Request, response: Response, db: Session = Depends(get_db)) -> dict[str, Any]:
    _set_public_cors(response, request)
    with managed_session(db):
        result = add_visitor_message(db, conversation_id, payload.visitor_token, payload.body, request)
    return result


@router.get("/conversations/{conversation_id}/messages")
def poll_webchat_messages(conversation_id: str, visitor_token: str, request: Request, response: Response, db: Session = Depends(get_db)) -> dict[str, Any]:
    _set_public_cors(response, request)
    with managed_session(db):
        result = list_public_messages(db, conversation_id, visitor_token)
    return result


@router.get("/admin/conversations")
def list_webchat_conversations(limit: int = 50, db: Session = Depends(get_db), current_user=Depends(get_current_user)) -> list[dict[str, Any]]:
    return admin_list_conversations(db, current_user, limit=limit)


@router.get("/admin/tickets/{ticket_id}/thread")
def get_webchat_thread(ticket_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)) -> dict[str, Any]:
    return admin_get_thread(db, ticket_id, current_user)


@router.post("/admin/tickets/{ticket_id}/reply")
def reply_webchat(ticket_id: int, payload: WebchatReplyRequest, db: Session = Depends(get_db), current_user=Depends(get_current_user)) -> dict[str, Any]:
    with managed_session(db):
        result = admin_reply(
            db,
            ticket_id,
            current_user,
            body=payload.body,
            has_fact_evidence=payload.has_fact_evidence,
            confirm_review=payload.confirm_review,
        )
    return result
