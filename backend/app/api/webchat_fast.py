from __future__ import annotations

from typing import Any, AsyncIterator
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from ..db import db_context
from ..settings import get_settings
from ..services.webchat_fast_ai_service import generate_webchat_fast_reply
from ..services.webchat_fast_idempotency_db import (
    WebchatFastIdempotency,
    begin_webchat_fast_idempotency,
    compute_request_hash,
    mark_webchat_fast_done,
    mark_webchat_fast_failed,
)
from ..services.webchat_fast_rate_limit import enforce_webchat_fast_rate_limit
from ..services.webchat_fast_stream_service import prepare_webchat_fast_stream, sse_event, stream_webchat_fast_reply_events
from ..services.webchat_handoff_policy import HandoffPolicyDecision, decide_server_handoff_policy
from ..services.webchat_handoff_snapshot_service import build_handoff_snapshot_payload, enqueue_webchat_handoff_snapshot_job
from ..services.webchat_fast_config import get_webchat_fast_settings, WebchatFastSettings
from ..services.webchat_fast_rollout import is_stream_rollout_selected


router = APIRouter(prefix="/api/webchat", tags=["webchat-fast"])
settings = get_settings()


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
        "Access-Control-Allow-Headers": "Content-Type, X-Requested-With, Accept",
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


def _handoff_enqueue_failure_payload(result: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "ai_generated": result.ai_generated,
        "reply_source": result.reply_source,
        "reply": None,
        "intent": result.intent,
        "tracking_number": result.tracking_number,
        "handoff_required": True,
        "handoff_reason": result.handoff_reason,
        "ticket_creation_queued": False,
        "elapsed_ms": result.elapsed_ms,
        "error_code": "handoff_enqueue_failed",
        "retry_after_ms": 1500,
    }


def _server_handoff_response_payload(*, handoff_reason: str | None, customer_reply: str | None) -> dict[str, Any]:
    return {
        "ok": True,
        "ai_generated": False,
        "reply_source": "server_handoff_policy",
        "reply": customer_reply or "A human teammate will review this request.",
        "intent": "handoff",
        "tracking_number": None,
        "handoff_required": True,
        "handoff_reason": handoff_reason or "server_policy_handoff_required",
        "ticket_creation_queued": False,
        "elapsed_ms": 0,
    }


def _server_handoff_enqueue_failure_payload(result_payload: dict[str, Any]) -> dict[str, Any]:
    failure = dict(result_payload)
    failure["ok"] = False
    failure["reply"] = None
    failure["ticket_creation_queued"] = False
    failure["error_code"] = "handoff_enqueue_failed"
    failure["retry_after_ms"] = 1500
    return failure


def _server_policy_handoff_snapshot(
    *,
    payload: WebchatFastReplyRequest,
    context_payload: list[dict[str, str]],
    server_policy: HandoffPolicyDecision,
    reply: str,
) -> dict[str, Any]:
    return build_handoff_snapshot_payload(
        tenant_key=payload.tenant_key,
        channel_key=payload.channel_key,
        session_id=payload.session_id,
        client_message_id=payload.client_message_id,
        customer_last_message=payload.body,
        ai_reply=reply,
        intent="handoff",
        tracking_number=None,
        handoff_reason=server_policy.handoff_reason,
        recommended_agent_action=server_policy.recommended_agent_action,
        recent_context=context_payload,
        visitor=_visitor_payload(payload.visitor),
    )


async def _server_policy_stream_events(
    *,
    row_id: int,
    payload: WebchatFastReplyRequest,
    context_payload: list[dict[str, str]],
    server_policy: HandoffPolicyDecision,
) -> AsyncIterator[str]:
    result_payload = _server_handoff_response_payload(
        handoff_reason=server_policy.handoff_reason,
        customer_reply=server_policy.customer_reply,
    )
    snapshot = _server_policy_handoff_snapshot(
        payload=payload,
        context_payload=context_payload,
        server_policy=server_policy,
        reply=result_payload["reply"],
    )
    try:
        with db_context() as db:
            enqueue_webchat_handoff_snapshot_job(db, snapshot=snapshot)
        result_payload["ticket_creation_queued"] = True
    except Exception:
        with db_context() as db:
            row = db.execute(select(WebchatFastIdempotency).where(WebchatFastIdempotency.id == row_id)).scalar_one()
            mark_webchat_fast_failed(db, row, error_code="handoff_enqueue_failed")
        yield sse_event("error", {"error_code": "handoff_enqueue_failed", "retry_after_ms": 1500})
        return

    with db_context() as db:
        row = db.execute(select(WebchatFastIdempotency).where(WebchatFastIdempotency.id == row_id)).scalar_one()
        mark_webchat_fast_done(db, row, response_json=result_payload)
    yield sse_event("meta", {"replayed": False, "stream_version": "V2.2.2", "reply_source": "server_handoff_policy"})
    yield sse_event("reply_delta", {"text": result_payload["reply"]})
    final = {k: v for k, v in result_payload.items() if k != "reply"}
    yield sse_event("final", final)


def _is_stream_canary_override_allowed(request: Request, settings: WebchatFastSettings) -> bool:
    canary_header = request.headers.get("x-nexus-stream-canary")
    if canary_header != "1":
        return False
        
    client_host = request.client.host if request.client else None
    if client_host in ("127.0.0.1", "::1"):
        return True
        
    if settings.app_env in {"development", "test", "local"}:
        return True
        
    return False

@router.options("/fast-reply")
def webchat_fast_reply_options(request: Request):
    return Response(status_code=204, headers=_public_cors_headers(request))


@router.options("/fast-reply/stream")
def webchat_fast_reply_stream_options(request: Request):
    headers = _public_cors_headers(request)
    headers["X-Accel-Buffering"] = "no"
    return Response(status_code=204, headers=headers)


@router.post("/fast-reply")
async def webchat_fast_reply(payload: WebchatFastReplyRequest, request: Request, response: Response) -> Response:
    _set_public_cors(response, request)
    enforce_webchat_fast_rate_limit(request, tenant_key=payload.tenant_key, session_id=payload.session_id)
    headers = _public_cors_headers(request)
    context_payload = _context_payload(payload.recent_context)

    request_hash = compute_request_hash(
        tenant_key=payload.tenant_key,
        channel_key=payload.channel_key,
        session_id=payload.session_id,
        client_message_id=payload.client_message_id,
        body=payload.body,
        recent_context=context_payload,
    )

    with db_context() as db:
        begin = begin_webchat_fast_idempotency(
            db,
            tenant_key=payload.tenant_key,
            session_id=payload.session_id,
            client_message_id=payload.client_message_id,
            request_hash=request_hash,
            owner_request_id=getattr(request.state, "request_id", None),
        )
        row_id = begin.row.id if begin.row is not None else None

    if begin.kind == "replay":
        replayed = dict(begin.response_json or {})
        replayed["idempotent"] = True
        return JSONResponse(replayed, status_code=200, headers=headers)
    if begin.kind == "processing":
        return JSONResponse({"error_code": "request_processing", "retry_after_ms": 1500}, status_code=202, headers=headers)
    if begin.kind == "conflict":
        return JSONResponse({"error_code": "idempotency_key_reused_with_different_payload"}, status_code=409, headers=headers)
    if begin.kind == "failed_non_retryable":
        return JSONResponse({"error_code": begin.error_code or "request_failed"}, status_code=409, headers=headers)
    if row_id is None:
        return JSONResponse({"error_code": "idempotency_error", "retry_after_ms": 1500}, status_code=500, headers=headers)

    server_policy = decide_server_handoff_policy(body=payload.body, recent_context=context_payload)
    if server_policy.handoff_required:
        result_payload = _server_handoff_response_payload(
            handoff_reason=server_policy.handoff_reason,
            customer_reply=server_policy.customer_reply,
        )
        snapshot = _server_policy_handoff_snapshot(
            payload=payload,
            context_payload=context_payload,
            server_policy=server_policy,
            reply=result_payload["reply"],
        )
        try:
            with db_context() as db:
                enqueue_webchat_handoff_snapshot_job(db, snapshot=snapshot)
            result_payload["ticket_creation_queued"] = True
        except Exception:
            with db_context() as db:
                row = db.execute(select(WebchatFastIdempotency).where(WebchatFastIdempotency.id == row_id)).scalar_one()
                mark_webchat_fast_failed(db, row, error_code="handoff_enqueue_failed")
            return JSONResponse(_server_handoff_enqueue_failure_payload(result_payload), status_code=503, headers=headers)
        with db_context() as db:
            row = db.execute(select(WebchatFastIdempotency).where(WebchatFastIdempotency.id == row_id)).scalar_one()
            mark_webchat_fast_done(db, row, response_json=result_payload)
        return JSONResponse(result_payload, status_code=200, headers=headers)

    result = await generate_webchat_fast_reply(
        tenant_key=payload.tenant_key,
        channel_key=payload.channel_key,
        session_id=payload.session_id,
        body=payload.body,
        recent_context=context_payload,
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
            recent_context=context_payload,
            visitor=_visitor_payload(payload.visitor),
        )
        try:
            with db_context() as db:
                enqueue_webchat_handoff_snapshot_job(db, snapshot=snapshot)
            result_payload["ticket_creation_queued"] = True
        except Exception:
            failure_payload = _handoff_enqueue_failure_payload(result)
            with db_context() as db:
                row = db.execute(select(WebchatFastIdempotency).where(WebchatFastIdempotency.id == row_id)).scalar_one()
                mark_webchat_fast_failed(db, row, error_code="handoff_enqueue_failed")
            return JSONResponse(failure_payload, status_code=503, headers=headers)

    with db_context() as db:
        row = db.execute(select(WebchatFastIdempotency).where(WebchatFastIdempotency.id == row_id)).scalar_one()
        if result.ok:
            mark_webchat_fast_done(db, row, response_json=result_payload)
        else:
            mark_webchat_fast_failed(db, row, error_code=result.error_code or "request_failed")
    return JSONResponse(result_payload, status_code=200, headers=headers)


@router.post("/fast-reply/stream")
async def webchat_fast_reply_stream(payload: WebchatFastReplyRequest, request: Request) -> Response:
    stream_settings = get_webchat_fast_settings()
    headers = _public_cors_headers(request)
    headers.update({
        "Content-Type": "text/event-stream",
        "X-Accel-Buffering": "no",
        "Cache-Control": "no-store",
        "Vary": "Origin",
    })

    if not stream_settings.stream_enabled:
        return JSONResponse({"error_code": "stream_disabled"}, status_code=503, headers=headers)
    if stream_settings.stream_require_accept and "text/event-stream" not in (request.headers.get("accept") or ""):
        return JSONResponse({"error_code": "stream_accept_required"}, status_code=406, headers=headers)

    if not stream_settings.is_openclaw_stream_configured:
        return JSONResponse({"error_code": "stream_upstream_not_configured"}, status_code=503, headers=headers)

    # Rollout gate
    is_selected = is_stream_rollout_selected(
        tenant_key=payload.tenant_key,
        channel_key=payload.channel_key,
        session_id=payload.session_id,
        rollout_percent=getattr(stream_settings, "stream_rollout_percent", 100)
    )
    if not is_selected and not _is_stream_canary_override_allowed(request, stream_settings):
        return JSONResponse({"error_code": "stream_not_in_rollout"}, status_code=503, headers=headers)

    enforce_webchat_fast_rate_limit(request, tenant_key=payload.tenant_key, session_id=payload.session_id)
    context_payload = _context_payload(payload.recent_context)

    begin = prepare_webchat_fast_stream(
        tenant_key=payload.tenant_key,
        channel_key=payload.channel_key,
        session_id=payload.session_id,
        client_message_id=payload.client_message_id,
        body=payload.body,
        recent_context=context_payload,
        request_id=getattr(request.state, "request_id", None),
    )
    if begin.status == "processing":
        return JSONResponse({"error_code": "request_processing", "retry_after_ms": 1500}, status_code=202, headers=headers)
    if begin.status == "conflict":
        return JSONResponse({"error_code": "idempotency_key_reused_with_different_payload"}, status_code=409, headers=headers)
    if begin.status == "failed_non_retryable":
        return JSONResponse({"error_code": begin.error_code or "request_failed"}, status_code=409, headers=headers)

    if begin.row_id is not None:
        server_policy = decide_server_handoff_policy(body=payload.body, recent_context=context_payload)
        if server_policy.handoff_required:
            generator = _server_policy_stream_events(
                row_id=begin.row_id,
                payload=payload,
                context_payload=context_payload,
                server_policy=server_policy,
            )
            return StreamingResponse(generator, media_type="text/event-stream", headers=headers)

    generator = stream_webchat_fast_reply_events(
        begin=begin,
        tenant_key=payload.tenant_key,
        channel_key=payload.channel_key,
        session_id=payload.session_id,
        client_message_id=payload.client_message_id,
        body=payload.body,
        recent_context=context_payload,
        visitor=payload.visitor,
        request_id=getattr(request.state, "request_id", None),
        settings=stream_settings,
    )
    return StreamingResponse(generator, media_type="text/event-stream", headers=headers)
