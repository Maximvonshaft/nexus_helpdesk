from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from ..db import db_context
from ..settings import get_settings
from ..services.webchat_fast_ai_service import generate_webchat_fast_reply
from ..services.webchat_fast_idempotency import get_fast_reply_idempotent_response, remember_fast_reply_response
from ..services.webchat_fast_rate_limit import enforce_webchat_fast_rate_limit
from ..services.webchat_handoff_snapshot_service import build_handoff_snapshot_payload, enqueue_webchat_handoff_snapshot_job

router = APIRouter(prefix="/api/webchat", tags=["webchat-fast"])
settings = get_settings()
LOGGER = logging.getLogger("nexusdesk")


class WebchatFastContextItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    role: str = Field(max_length=40)
    text: str = Field(max_length=500)


class WebchatFastVisitor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=160)
    email: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=80)


class WebchatFastReplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_key: str = Field(default="default", max_length=120)
    channel_key: str = Field(default="website", max_length=120)
    session_id: str = Field(min_length=4, max_length=120)
    client_message_id: str = Field(min_length=4, max_length=120)
    body: str = Field(min_length=1, max_length=2000)
    recent_context: list[WebchatFastContextItem] = Field(default_factory=list, max_length=10)
    visitor: WebchatFastVisitor | None = None


def _normalized_allowed_origins() -> set[str]:
    allowed = {item.rstrip("/") for item in settings.webchat_allowed_origins if item.strip()}
    if settings.app_env in {"development", "test", "local"}:
        allowed.update({"http://localhost", "http://127.0.0.1"})
    return allowed


def _validated_origin(request: Request) -> str | None:
    origin = request.headers.get("origin")
    allowed = _normalized_allowed_origins()
    if origin:
        normalized = origin.rstrip("/")
        if normalized not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Webchat origin is not allowed")
        return origin
    referer = request.headers.get("referer")
    if referer:
        parsed = urlparse(referer)
        if parsed.scheme and parsed.netloc:
            referer_origin = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
            if referer_origin in allowed:
                return referer_origin
    if settings.webchat_allow_no_origin or settings.app_env in {"development", "test", "local"}:
        return None
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Webchat origin is required")


def _public_cors_headers(request: Request) -> dict[str, str]:
    origin = _validated_origin(request)
    headers = {
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, X-Requested-With",
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


def _context_payload(items: list[WebchatFastContextItem]) -> list[dict[str, str]]:
    return [{"role": item.role, "text": item.text} for item in items[-10:]]


def _visitor_payload(visitor: WebchatFastVisitor | None) -> dict[str, Any]:
    return visitor.model_dump(exclude_none=True) if visitor else {}


@router.options("/fast-reply")
def webchat_fast_reply_options(request: Request):
    return Response(status_code=204, headers=_public_cors_headers(request))


@router.post("/fast-reply")
async def webchat_fast_reply(payload: WebchatFastReplyRequest, request: Request, response: Response) -> dict[str, Any]:
    _set_public_cors(response, request)
    enforce_webchat_fast_rate_limit(request, tenant_key=payload.tenant_key, session_id=payload.session_id)

    existing = get_fast_reply_idempotent_response(
        tenant_key=payload.tenant_key,
        session_id=payload.session_id,
        client_message_id=payload.client_message_id,
    )
    if existing is not None:
        existing["idempotent"] = True
        return existing

    result = await generate_webchat_fast_reply(
        tenant_key=payload.tenant_key,
        channel_key=payload.channel_key,
        session_id=payload.session_id,
        body=payload.body,
        recent_context=_context_payload(payload.recent_context),
        request_id=getattr(request.state, "request_id", None),
    )
    result_payload = result.to_response()

    if result.ok and result.handoff_required and result.reply:
        snapshot = build_handoff_snapshot_payload(
            tenant_key=payload.tenant_key,
            channel_key=payload.channel_key,
            session_id=payload.session_id,
            client_message_id=payload.client_message_id,
            customer_last_message=payload.body,
            ai_reply=result.reply,
            intent=result.intent,
            tracking_number=result.tracking_number,
            handoff_reason=result.handoff_reason,
            recommended_agent_action=result.recommended_agent_action,
            recent_context=_context_payload(payload.recent_context),
            visitor=_visitor_payload(payload.visitor),
        )
        try:
            with db_context() as db:
                enqueue_webchat_handoff_snapshot_job(db, snapshot=snapshot)
            result_payload["ticket_creation_queued"] = True
        except Exception as exc:
            LOGGER.warning(
                "webchat_fast_handoff_enqueue_failed",
                extra={
                    "event_payload": {
                        "tenant_key": payload.tenant_key,
                        "channel_key": payload.channel_key,
                        "request_id": getattr(request.state, "request_id", None),
                        "error_type": type(exc).__name__,
                    }
                },
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error_code": "handoff_enqueue_failed",
                    "message": "Unable to queue human handoff. Please retry.",
                    "retry_after_ms": 1500,
                },
            ) from exc

    remember_fast_reply_response(
        tenant_key=payload.tenant_key,
        session_id=payload.session_id,
        client_message_id=payload.client_message_id,
        response=result_payload,
    )
    return result_payload
