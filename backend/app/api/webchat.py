from __future__ import annotations

import hashlib
import json
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
from ..webchat_models import WebchatCardAction, WebchatConversation, WebchatMessage
from ..webchat_schemas import WebChatActionSubmitRequest
from ..services.webchat_service import (
    add_visitor_message,
    admin_get_thread,
    admin_list_conversations,
    admin_reply,
    create_or_resume_conversation,
    list_public_messages,
    submit_card_action,
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
    client_message_id: str | None = Field(default=None, max_length=120)


class WebchatReplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=1, max_length=2000)
    has_fact_evidence: bool = False
    confirm_review: bool = False


def _normalized_allowed_origins() -> set[str]:
    allowed = {item.rstrip("/") for item in settings.webchat_allowed_origins if item.strip()}
    if settings.app_env in {"development", "test", "local"}:
        allowed.update({"http://localhost", "http://127.0.0.1"})
    return allowed


def _validated_origin(request: Request) -> str | None:
    origin = request.headers.get("origin")
    if not origin:
        if settings.webchat_allow_no_origin or settings.app_env in {"development", "test", "local"}:
            return None
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Webchat origin is required")
    normalized = origin.rstrip("/")
    if normalized not in _normalized_allowed_origins():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Webchat origin is not allowed")
    return origin


def _public_cors_headers(request: Request) -> dict[str, str]:
    origin = _validated_origin(request)
    headers = {
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, X-Requested-With, X-Webchat-Visitor-Token",
        "Access-Control-Max-Age": "600",
        "Vary": "Origin",
        "Cache-Control": "no-store",
    }
    if origin:
        headers["Access-Control-Allow-Origin"] = origin
    return headers


def _set_public_cors(response: Response, request: Request) -> None:
    for key, value in _public_cors_headers(request).items():
        response.headers.setdefault(key, value)


def _legacy_token_transport_enabled() -> bool:
    return os.getenv("WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT", "false").strip().lower() in {"1", "true", "yes", "on"}


def _resolve_visitor_token(header_token: str | None, query_token: str | None, body_token: str | None = None) -> str | None:
    # Header is the production-safe transport. Query/body compatibility is opt-in only.
    if header_token:
        return header_token
    if _legacy_token_transport_enabled():
        return body_token or query_token
    return None


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _loads_json(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def _message_read(row: WebchatMessage) -> dict[str, Any]:
    body_text = getattr(row, "body_text", None) or row.body
    return {
        "id": row.id,
        "direction": row.direction,
        "body": row.body,
        "body_text": body_text,
        "message_type": getattr(row, "message_type", None) or "text",
        "payload_json": _loads_json(getattr(row, "payload_json", None)),
        "metadata_json": _loads_json(getattr(row, "metadata_json", None)),
        "client_message_id": getattr(row, "client_message_id", None),
        "delivery_status": getattr(row, "delivery_status", None) or "sent",
        "action_status": getattr(row, "action_status", None),
        "author_label": row.author_label,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _find_existing_action_response(
    db: Session,
    *,
    public_conversation_id: str,
    visitor_token: str,
    payload: WebChatActionSubmitRequest,
) -> dict[str, Any] | None:
    """Return an existing action response for repeated card-action submissions.

    This is intentionally conservative: it only treats an action as idempotent
    after the visitor token is verified and the same card message + action_id has
    already produced a `webchat_card_actions` row. It prevents double-clicks and
    network retries from creating duplicate action audit rows or ticket comments.
    """
    conversation = db.query(WebchatConversation).filter(WebchatConversation.public_id == public_conversation_id).first()
    if not conversation or _hash_token(visitor_token) != conversation.visitor_token_hash:
        return None

    candidates = (
        db.query(WebchatCardAction)
        .filter(
            WebchatCardAction.conversation_id == conversation.id,
            WebchatCardAction.message_id == payload.message_id,
            WebchatCardAction.submitted_by == "visitor",
        )
        .order_by(WebchatCardAction.id.asc())
        .all()
    )
    for action in candidates:
        stored_payload = _loads_json(action.action_payload_json) or {}
        if stored_payload.get("action_id") != payload.action_id:
            continue
        message = (
            db.query(WebchatMessage)
            .filter(
                WebchatMessage.conversation_id == conversation.id,
                WebchatMessage.message_type == "action",
                WebchatMessage.payload_json.like(f'%"action_id": "{payload.action_id}"%'),
            )
            .order_by(WebchatMessage.id.asc())
            .first()
        )
        if not message:
            return None
        return {
            "ok": True,
            "idempotent": True,
            "action_id": action.id,
            "status": action.status,
            "message": _message_read(message),
            "handoff_triggered": payload.action_type == "handoff_request" or stored_payload.get("card_type") == "handoff" or payload.action_id == "talk_to_human",
        }
    return None


@router.options("/{full_path:path}")
def webchat_options(full_path: str, request: Request):
    return Response(status_code=204, headers=_public_cors_headers(request))


@router.post("/init")
def init_webchat(
    payload: WebchatInitRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    x_webchat_visitor_token: str | None = Header(default=None, alias="X-Webchat-Visitor-Token"),
) -> dict[str, Any]:
    _set_public_cors(response, request)
    visitor_token = _resolve_visitor_token(x_webchat_visitor_token, None, payload.visitor_token)
    safe_payload = payload.model_copy(update={"visitor_token": visitor_token})
    with managed_session(db):
        enforce_webchat_rate_limit(db, request, tenant_key=payload.tenant_key, conversation_id=payload.conversation_id)
        result = create_or_resume_conversation(db, safe_payload, request)
    return result


@router.post("/conversations/{conversation_id}/messages")
def send_webchat_message(
    conversation_id: str,
    payload: WebchatSendRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    x_webchat_visitor_token: str | None = Header(default=None, alias="X-Webchat-Visitor-Token"),
) -> dict[str, Any]:
    _set_public_cors(response, request)
    visitor_token = _resolve_visitor_token(x_webchat_visitor_token, None, payload.visitor_token)
    if not visitor_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid webchat visitor token")
    with managed_session(db):
        enforce_webchat_rate_limit(db, request, tenant_key="default", conversation_id=conversation_id)
        result = add_visitor_message(db, conversation_id, visitor_token, payload.body, request, client_message_id=payload.client_message_id)
    return result


@router.get("/conversations/{conversation_id}/messages")
def poll_webchat_messages(
    conversation_id: str,
    request: Request,
    response: Response,
    visitor_token: str | None = Query(default=None),
    after_id: int | None = Query(default=None, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    x_webchat_visitor_token: str | None = Header(default=None, alias="X-Webchat-Visitor-Token"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    _set_public_cors(response, request)
    resolved_token = _resolve_visitor_token(x_webchat_visitor_token, visitor_token)
    if not resolved_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid webchat visitor token")
    with managed_session(db):
        enforce_webchat_rate_limit(db, request, tenant_key="default", conversation_id=conversation_id)
        result = list_public_messages(db, conversation_id, resolved_token, after_id=after_id, limit=limit)
    return result


@router.post("/conversations/{conversation_id}/actions")
def submit_webchat_action(
    conversation_id: str,
    payload: WebChatActionSubmitRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    x_webchat_visitor_token: str | None = Header(default=None, alias="X-Webchat-Visitor-Token"),
) -> dict[str, Any]:
    _set_public_cors(response, request)
    visitor_token = _resolve_visitor_token(x_webchat_visitor_token, None, payload.visitor_token)
    if not visitor_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid webchat visitor token")
    with managed_session(db):
        enforce_webchat_rate_limit(db, request, tenant_key="default", conversation_id=conversation_id)
        existing = _find_existing_action_response(db, public_conversation_id=conversation_id, visitor_token=visitor_token, payload=payload)
        if existing:
            return existing
        result = submit_card_action(db, conversation_id, visitor_token, payload, request)
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
