from __future__ import annotations

import logging
from typing import Any, AsyncIterator
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from ..db import db_context
from ..settings import get_settings
from ..services.background_jobs import enqueue_speedaf_work_order_create_job
from ..services.tracking_fact_schema import TrackingFactResult, hash_tracking_number
from ..services.tracking_fact_service import extract_tracking_number, lookup_tracking_fact
from ..services.webchat_fast_ai_service import generate_webchat_fast_reply
from ..services.webchat_fast_idempotency_db import (
    WebchatFastIdempotency,
    begin_webchat_fast_idempotency,
    compute_legacy_v1_request_hash_aliases,
    compute_request_hash,
    mark_webchat_fast_done,
    mark_webchat_fast_failed,
)
from ..services.webchat_fast_rate_limit import enforce_webchat_fast_rate_limit
from ..services.webchat_fast_stream_service import prepare_webchat_fast_stream, sse_event, stream_webchat_fast_reply_events
from ..services.webchat_handoff_policy import HandoffPolicyDecision, decide_server_handoff_policy
from ..services.webchat_handoff_policy_config import load_webchat_handoff_rules
from ..services.webchat_fast_config import get_webchat_fast_settings, WebchatFastSettings
from ..services.webchat_fast_rollout import is_stream_rollout_selected
from ..services.webchat_fast_session_service import (
    FastBusinessState,
    FastRoutingContext,
    append_fast_ai_message,
    append_fast_system_handoff_message,
    append_fast_visitor_message,
    build_fast_server_context,
    extract_fast_business_state,
    get_or_create_fast_conversation,
    get_or_create_fast_ticket,
    merge_fast_context,
    resolve_fast_routing_context,
    update_fast_business_state,
)
from app.services.webchat_fast_policy import match_support_hours_policy_reply

router = APIRouter(prefix="/api/webchat", tags=["webchat-fast"])
settings = get_settings()
LOGGER = logging.getLogger("nexusdesk")


class WebchatFastContextItem(BaseModel):
    role: str = Field(default="visitor", max_length=32)
    text: str = Field(max_length=500)

    @model_validator(mode="before")
    @classmethod
    def __webchat_recent_context_compat_v1(cls, value):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if data.get("text") is None:
            for key in ("body", "content", "message"):
                if data.get(key) is not None:
                    data["text"] = data.get(key)
                    break
        role = str(data.get("role") or "").strip().lower()
        data["role"] = "agent" if role in {"assistant", "agent", "ai", "bot", "support", "system"} else "visitor"
        return data


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
    country_code: str | None = Field(default=None, max_length=8)
    market_code: str | None = Field(default=None, max_length=16)
    channel_account_key: str | None = Field(default=None, max_length=160)


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
        if parsed.scheme and parsed.netloc and f"{parsed.scheme}://{parsed.netloc}".rstrip("/") in allowed:
            return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
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
    return [{"role": "visitor", "text": item.text} for item in items[-10:] if item.role == "visitor"]


def _trusted_context(server_context: list[dict[str, Any]] | None, frontend_context: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    if server_context:
        return merge_fast_context(server_context, [])
    visitor_only = [item for item in (frontend_context or []) if str(item.get("role") or "").lower() == "visitor"]
    return merge_fast_context([], visitor_only)


def _caller_id(visitor: WebchatFastVisitor | None) -> str | None:
    cleaned = " ".join(str((visitor.phone if visitor else None) or "").strip().split())
    return cleaned[:80] if cleaned else None


def _context_values(body: str, context: list[dict[str, Any]] | None, tracking_number: str | None) -> list[str | None]:
    values: list[str | None] = [tracking_number, body]
    values.extend(str(item.get("text") or item.get("body") or "") for item in (context or []) if isinstance(item, dict))
    return values


def _tracking_candidate(*, body: str, context: list[dict[str, Any]] | None, tracking_number: str | None) -> str | None:
    cleaned = (tracking_number or "").strip().upper()
    return cleaned or extract_tracking_number(*_context_values(body, context, tracking_number))


def _tracking_fact_public_payload(result: TrackingFactResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    metadata = result.metadata_payload()
    payload = {
        "tool_status": result.tool_status,
        "fact_evidence_present": result.fact_evidence_present,
        "pii_redacted": result.pii_redacted,
        "failure_reason": result.failure_reason,
        "tracking_number_hash": metadata.get("tracking_number_hash"),
    }
    if result.fact_evidence_present and result.pii_redacted:
        if result.status:
            payload["status"] = result.status
        if result.status_label:
            payload["status_label"] = result.status_label
    if metadata.get("safe_candidates"):
        payload["safe_candidates"] = metadata.get("safe_candidates")
        payload["candidate_count"] = metadata.get("candidate_count")
    return payload


def _tracking_candidate_selection_payload(result: TrackingFactResult | None) -> dict[str, Any] | None:
    if result is None or result.failure_reason != "multiple_waybill_candidates":
        return None
    metadata = result.metadata_payload()
    candidates = [item for item in metadata.get("safe_candidates") or [] if item.get("waybill_suffix")]
    if not candidates:
        return None
    suffixes = ", ".join(str(item["waybill_suffix"]) for item in candidates)
    return {"ok": True, "ai_generated": False, "reply_source": "server_tracking_candidate_selection", "reply": f"I found multiple shipments linked to this phone number. Please reply with the last 4 digits of the shipment you want to check: {suffixes}", "intent": "tracking_candidate_selection", "tracking_number": None, "handoff_required": False, "handoff_reason": None, "ticket_creation_queued": False, "elapsed_ms": 0, "safe_candidates": candidates}



# STREAM_ROUTE_FORCE_ENABLE_BEGIN
def _webchat_stream_route_forced_enabled() -> bool:
    truthy = {"1", "true", "yes", "y", "on", "enabled"}
    for key in (
        "WEBCHAT_FAST_REPLY_STREAM_ENABLED",
        "WEBCHAT_FAST_REPLY_STREAMING_ENABLED",
        "WEBCHAT_FAST_REPLY_STREAM_ROUTE_ENABLED",
        "WEBCHAT_FAST_STREAM_ENABLED",
        "WEBCHAT_STREAM_ENABLED",
        "WEBCHAT_STREAMING_ENABLED",
        "WEBCHAT_ENABLE_STREAM",
    ):
        value = str(__import__("os").environ.get(key, "")).strip().lower()
        if value in truthy:
            return True
    return False
# STREAM_ROUTE_FORCE_ENABLE_END

def _tracking_fact_forced_reply_payload(*, tracking_number: str | None, result: TrackingFactResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    if not bool(result.ok and result.fact_evidence_present and result.pii_redacted):
        return None

    status_code = str(result.status or "").strip()
    status_label = str(result.status_label or "").strip()
    if not status_code and not status_label:
        return None

    safe_number = str(result.tracking_number or tracking_number or "").strip().upper()
    suffix = safe_number[-6:] if safe_number else ""
    parcel_ref = f"ending {suffix}" if suffix else "provided by you"

    if status_code and status_label:
        reply = f"Your parcel {parcel_ref} is currently {status_label} (status code: {status_code})."
    elif status_label:
        reply = f"Your parcel {parcel_ref} is currently {status_label}."
    else:
        reply = f"Your parcel {parcel_ref} currently has status code {status_code}."

    return {
        "ok": True,
        "ai_generated": False,
        "reply_source": "server_tracking_fact",
        "reply": reply,
        "intent": "tracking",
        "tracking_number": safe_number or None,
        "handoff_required": False,
        "handoff_reason": None,
        "ticket_creation_queued": False,
        "elapsed_ms": 0,
        "tracking_fact": _tracking_fact_public_payload(result),
    }


def _should_attempt_fact_first_lookup(*, body: str | None, tracking_number: str | None, caller_id: str | None) -> bool:
    if tracking_number:
        return True
    if not caller_id:
        return False
    text = (body or "").lower()
    markers = (
        "track", "tracking", "parcel", "package", "shipment", "waybill", "status",
        "where is", "where's", "delivery",
        "查件", "查询", "物流", "包裹", "快递", "单号", "运单", "派送", "签收", "妥投"
    )
    return any(marker in text for marker in markers)


def _persist_tracking_fact_forced_reply(
    *,
    row_id: int,
    payload: WebchatFastReplyRequest,
    result_payload: dict[str, Any],
    tracking_fact_metadata: dict[str, Any] | None,
    request: Request | None = None,
) -> None:
    with db_context() as db:
        conversation = get_or_create_fast_conversation(
            db,
            tenant_key=payload.tenant_key,
            channel_key=payload.channel_key,
            session_id=payload.session_id,
            request=request,
            visitor=payload.visitor,
        )
        append_fast_ai_message(
            db,
            conversation=conversation,
            reply=result_payload["reply"],
            client_message_id=payload.client_message_id,
            metadata={
                "handoff_required": False,
                "source": "server_tracking_fact",
                "tracking_fact": tracking_fact_metadata or {},
            },
        )
        row = db.execute(select(WebchatFastIdempotency).where(WebchatFastIdempotency.id == row_id)).scalar_one()
        mark_webchat_fast_done(db, row, response_json=result_payload)


async def _tracking_fact_forced_stream_events(
    *,
    row_id: int,
    payload: WebchatFastReplyRequest,
    result_payload: dict[str, Any],
    tracking_fact_metadata: dict[str, Any] | None,
    request: Request | None = None,
) -> AsyncIterator[str]:
    _persist_tracking_fact_forced_reply(
        row_id=row_id,
        payload=payload,
        result_payload=result_payload,
        tracking_fact_metadata=tracking_fact_metadata,
        request=request,
    )
    yield sse_event("meta", {"replayed": False, "stream_version": "V2.2.2", "reply_source": "server_tracking_fact"})
    yield sse_event("reply_delta", {"text": result_payload["reply"]})
    yield sse_event("final", {k: v for k, v in result_payload.items() if k != "reply"})


def _is_delivery_follow_up_request(*, body: str | None, business_state: FastBusinessState, handoff_reason: str | None = None, recommended_action: str | None = None) -> bool:
    text = " ".join([body or "", business_state.intent or "", business_state.issue_type or "", handoff_reason or "", recommended_action or ""]).lower()
    markers = ("催派", "催一下", "尽快派送", "加急派送", "还没到", "没有派送", "urge delivery", "delivery follow", "follow up delivery", "too slow", "late delivery", "not delivered", "still not delivered", "where is my parcel", "where is my package", "redelivery", "reschedule", "deliver again")
    return business_state.issue_type == "delivery_reschedule" or any(marker in text for marker in markers)


def _maybe_enqueue_speedaf_work_order(*, db, ticket_id: int, conversation_id: int | None, business_state: FastBusinessState, body: str, visitor: WebchatFastVisitor | None, handoff_reason: str | None = None, recommended_action: str | None = None) -> int | None:
    caller_id = _caller_id(visitor)
    waybill_code = (business_state.tracking_number or "").strip().upper()
    if not caller_id or not waybill_code or not _is_delivery_follow_up_request(body=body, business_state=business_state, handoff_reason=handoff_reason, recommended_action=recommended_action):
        return None
    job = enqueue_speedaf_work_order_create_job(db=db, ticket_id=ticket_id, conversation_id=conversation_id, waybill_code=waybill_code, caller_id=caller_id, description=f"WebChat delivery follow-up request: {body}"[:200], work_order_type="WT0103-05")
    return job.id


def _lookup_fast_tracking_fact(*, tracking_number: str | None, conversation_id: int | None, ticket_id: int | None, request_id: str | None, caller_id: str | None = None, country_code: str | None = None) -> TrackingFactResult | None:
    if not tracking_number and not caller_id:
        LOGGER.info("webchat_fast_tracking_fact_not_used", extra={"event_payload": {"reason": "missing_tracking_number_and_caller", "conversation_id": conversation_id, "request_id": request_id}})
        return None
    LOGGER.info("webchat_fast_tracking_fact_lookup_started", extra={"event_payload": {"conversation_id": conversation_id, "ticket_id": ticket_id, "request_id": request_id, "tracking_number_hash": hash_tracking_number(tracking_number), "caller_id_present": bool(caller_id), "country_code": country_code}})
    try:
        result = lookup_tracking_fact(tracking_number=tracking_number, caller_id=caller_id, country_code=country_code, conversation_id=conversation_id, ticket_id=ticket_id, request_id=request_id)
    except Exception as exc:
        LOGGER.warning("webchat_fast_tracking_fact_lookup_failed", extra={"event_payload": {"conversation_id": conversation_id, "ticket_id": ticket_id, "request_id": request_id, "tracking_number_hash": hash_tracking_number(tracking_number), "error_type": type(exc).__name__}})
        return None
    LOGGER.info("webchat_fast_tracking_fact_lookup_result", extra={"event_payload": {"conversation_id": conversation_id, "ticket_id": ticket_id, "request_id": request_id, **(_tracking_fact_public_payload(result) or {})}})
    return result


def _tracking_fact_provider_fields(result: TrackingFactResult | None) -> tuple[str | None, dict[str, Any] | None, bool]:
    if result is None:
        return None, None, False
    metadata = result.metadata_payload()
    if not bool(result.fact_evidence_present and result.pii_redacted):
        return None, metadata, False
    summary = result.prompt_summary().strip()
    return (summary, metadata, True) if summary else (None, metadata, False)


def _handoff_enqueue_failure_payload(result: Any) -> dict[str, Any]:
    return {"ok": False, "ai_generated": result.ai_generated, "reply_source": result.reply_source, "reply": None, "intent": result.intent, "tracking_number": result.tracking_number, "handoff_required": True, "handoff_reason": result.handoff_reason, "ticket_creation_queued": False, "elapsed_ms": result.elapsed_ms, "error_code": "handoff_enqueue_failed", "retry_after_ms": 1500}


def _server_handoff_response_payload(*, handoff_reason: str | None, customer_reply: str | None) -> dict[str, Any]:
    return {"ok": True, "ai_generated": False, "reply_source": "server_handoff_policy", "reply": customer_reply or "A human teammate will review this request.", "intent": "handoff", "tracking_number": None, "handoff_required": True, "handoff_reason": handoff_reason or "server_policy_handoff_required", "ticket_creation_queued": False, "elapsed_ms": 0}


def _support_hours_policy_payload(body: str) -> dict[str, Any] | None:
    return match_support_hours_policy_reply(body)


def _persist_support_hours_policy_reply(*, row_id: int, payload: WebchatFastReplyRequest, result_payload: dict[str, Any], request: Request | None = None) -> None:
    with db_context() as db:
        conversation = get_or_create_fast_conversation(db, tenant_key=payload.tenant_key, channel_key=payload.channel_key, session_id=payload.session_id, request=request, visitor=payload.visitor)
        append_fast_ai_message(db, conversation=conversation, reply=result_payload["reply"], client_message_id=payload.client_message_id, metadata={"handoff_required": False, "source": "server_support_hours_policy"})
        row = db.execute(select(WebchatFastIdempotency).where(WebchatFastIdempotency.id == row_id)).scalar_one()
        mark_webchat_fast_done(db, row, response_json=result_payload)


async def _support_hours_policy_stream_events(*, row_id: int, payload: WebchatFastReplyRequest, result_payload: dict[str, Any], request: Request | None = None) -> AsyncIterator[str]:
    _persist_support_hours_policy_reply(row_id=row_id, payload=payload, result_payload=result_payload, request=request)
    yield sse_event("meta", {"replayed": False, "stream_version": "V2.2.2", "reply_source": "server_support_hours_policy"})
    yield sse_event("reply_delta", {"text": result_payload["reply"]})
    yield sse_event("final", {k: v for k, v in result_payload.items() if k != "reply"})


async def _server_policy_stream_events(*, row_id: int, payload: WebchatFastReplyRequest, context_payload: list[dict[str, str]], server_policy: HandoffPolicyDecision, routing_context: FastRoutingContext) -> AsyncIterator[str]:
    result_payload = _server_handoff_response_payload(handoff_reason=server_policy.handoff_reason, customer_reply=server_policy.customer_reply)
    with db_context() as db:
        conversation = get_or_create_fast_conversation(db, tenant_key=payload.tenant_key, channel_key=payload.channel_key, session_id=payload.session_id)
        merged_context = _trusted_context(build_fast_server_context(db, conversation=conversation), context_payload)
        business_state = extract_fast_business_state(body=payload.body, context=merged_context, session_id=payload.session_id)
        update_fast_business_state(db, conversation=conversation, business_state=business_state, client_message_id=payload.client_message_id)
        ticket = get_or_create_fast_ticket(db, conversation=conversation, business_state=business_state, handoff_reason=server_policy.handoff_reason, recommended_agent_action=server_policy.recommended_agent_action, customer_message=payload.body, routing_context=routing_context)
        speedaf_job_id = _maybe_enqueue_speedaf_work_order(db=db, ticket_id=ticket.id, conversation_id=conversation.id, business_state=business_state, body=payload.body, visitor=payload.visitor, handoff_reason=server_policy.handoff_reason, recommended_action=server_policy.recommended_agent_action)
        append_fast_ai_message(db, conversation=conversation, reply=result_payload["reply"], client_message_id=payload.client_message_id, metadata={"handoff_required": True, "source": "server_handoff_policy", "speedaf_work_order_job_id": speedaf_job_id})
        append_fast_system_handoff_message(db, conversation=conversation, handoff_reason=server_policy.handoff_reason, recommended_agent_action=server_policy.recommended_agent_action, client_message_id=payload.client_message_id)
        result_payload.update({"ticket_id": ticket.id, "tracking_number": business_state.tracking_number})
        if speedaf_job_id:
            result_payload["speedaf_work_order_job_id"] = speedaf_job_id
        row = db.execute(select(WebchatFastIdempotency).where(WebchatFastIdempotency.id == row_id)).scalar_one()
        mark_webchat_fast_done(db, row, response_json=result_payload)
    yield sse_event("meta", {"replayed": False, "stream_version": "V2.2.2", "reply_source": "server_handoff_policy"})
    yield sse_event("reply_delta", {"text": result_payload["reply"]})
    yield sse_event("final", {k: v for k, v in result_payload.items() if k != "reply"})


async def _tracking_candidate_selection_stream_events(*, row_id: int, payload: WebchatFastReplyRequest, result_payload: dict[str, Any]) -> AsyncIterator[str]:
    with db_context() as db:
        conversation = get_or_create_fast_conversation(db, tenant_key=payload.tenant_key, channel_key=payload.channel_key, session_id=payload.session_id)
        append_fast_ai_message(db, conversation=conversation, reply=result_payload["reply"], client_message_id=payload.client_message_id, metadata={"source": "server_tracking_candidate_selection", "safe_candidates": result_payload.get("safe_candidates")})
        row = db.execute(select(WebchatFastIdempotency).where(WebchatFastIdempotency.id == row_id)).scalar_one()
        mark_webchat_fast_done(db, row, response_json=result_payload)
    yield sse_event("meta", {"replayed": False, "stream_version": "V2.2.2", "reply_source": "server_tracking_candidate_selection"})
    yield sse_event("final", {k: v for k, v in result_payload.items() if k != "reply"})
    yield sse_event("reply_delta", {"text": result_payload["reply"]})


def _is_stream_canary_override_allowed(request: Request, settings: WebchatFastSettings) -> bool:
    canary_header = request.headers.get("x-nexus-stream-canary")
    if canary_header != "1":
        return False
    client_host = request.client.host if request.client else None
    return client_host in ("127.0.0.1", "::1") or getattr(settings, "app_env", "development") in {"development", "test", "local"}


@router.options("/fast-reply")
def webchat_fast_reply_options(request: Request):
    return Response(status_code=204, headers=_public_cors_headers(request))


@router.options("/fast-reply/stream")
def webchat_fast_reply_stream_options(request: Request):
    headers = _public_cors_headers(request)
    headers["X-Accel-Buffering"] = "no"
    return Response(status_code=204, headers=headers)


def _begin_status_response(begin, headers: dict[str, str]) -> JSONResponse | None:
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
    return None


def _prepare_request_hashes(payload: WebchatFastReplyRequest, frontend_context: list[dict[str, str]]) -> tuple[str, tuple[str, ...]]:
    kwargs = dict(tenant_key=payload.tenant_key, channel_key=payload.channel_key, session_id=payload.session_id, client_message_id=payload.client_message_id, body=payload.body, recent_context=frontend_context)
    return compute_request_hash(**kwargs), compute_legacy_v1_request_hash_aliases(**kwargs)


@router.post("/fast-reply")
async def webchat_fast_reply(payload: WebchatFastReplyRequest, request: Request, response: Response) -> Response:
    _set_public_cors(response, request)
    enforce_webchat_fast_rate_limit(request, tenant_key=payload.tenant_key, session_id=payload.session_id)
    headers = _public_cors_headers(request)
    frontend_context = _context_payload(payload.recent_context)
    request_hash, request_hash_aliases = _prepare_request_hashes(payload, frontend_context)
    caller_id = _caller_id(payload.visitor)
    with db_context() as db:
        begin = begin_webchat_fast_idempotency(db, tenant_key=payload.tenant_key, session_id=payload.session_id, client_message_id=payload.client_message_id, request_hash=request_hash, request_hash_aliases=request_hash_aliases, owner_request_id=getattr(request.state, "request_id", None))
        row_id = begin.row.id if begin.row is not None else None
    status_response = _begin_status_response(begin, headers)
    if status_response is not None:
        return status_response
    if row_id is None:
        return JSONResponse({"error_code": "idempotency_error", "retry_after_ms": 1500}, status_code=500, headers=headers)

    with db_context() as db:
        conversation = get_or_create_fast_conversation(db, tenant_key=payload.tenant_key, channel_key=payload.channel_key, session_id=payload.session_id, request=request, visitor=payload.visitor)
        visitor_message = append_fast_visitor_message(db, conversation=conversation, body=payload.body, client_message_id=payload.client_message_id, metadata={"source": "webchat_fast"})
        merged_context = _trusted_context(build_fast_server_context(db, conversation=conversation, exclude_message_id=visitor_message.id), frontend_context)
        business_state = extract_fast_business_state(body=payload.body, context=merged_context, session_id=payload.session_id)
        update_fast_business_state(db, conversation=conversation, business_state=business_state, client_message_id=payload.client_message_id)
        conversation_id = conversation.id
        routing_context = resolve_fast_routing_context(db, country_code=payload.country_code, market_code=payload.market_code, channel_account_key=payload.channel_account_key)
        configured_rules = load_webchat_handoff_rules(db, market_id=routing_context.market_id, country_code=routing_context.country_code)


    support_payload = _support_hours_policy_payload(payload.body)
    if support_payload is not None:
        _persist_support_hours_policy_reply(row_id=row_id, payload=payload, result_payload=support_payload, request=request)
        return JSONResponse(support_payload, status_code=200, headers=headers)

    server_policy = decide_server_handoff_policy(body=payload.body, recent_context=merged_context, configured_rules=configured_rules)
    if server_policy.handoff_required:
        result_payload = _server_handoff_response_payload(handoff_reason=server_policy.handoff_reason, customer_reply=server_policy.customer_reply)
        with db_context() as db:
            conversation = get_or_create_fast_conversation(db, tenant_key=payload.tenant_key, channel_key=payload.channel_key, session_id=payload.session_id, request=request, visitor=payload.visitor)
            ticket = get_or_create_fast_ticket(db, conversation=conversation, business_state=business_state, handoff_reason=server_policy.handoff_reason, recommended_agent_action=server_policy.recommended_agent_action, customer_message=payload.body, routing_context=routing_context)
            speedaf_job_id = _maybe_enqueue_speedaf_work_order(db=db, ticket_id=ticket.id, conversation_id=conversation.id, business_state=business_state, body=payload.body, visitor=payload.visitor, handoff_reason=server_policy.handoff_reason, recommended_action=server_policy.recommended_agent_action)
            append_fast_ai_message(db, conversation=conversation, reply=result_payload["reply"], client_message_id=payload.client_message_id, metadata={"handoff_required": True, "source": "server_handoff_policy", "speedaf_work_order_job_id": speedaf_job_id})
            append_fast_system_handoff_message(db, conversation=conversation, handoff_reason=server_policy.handoff_reason, recommended_agent_action=server_policy.recommended_agent_action, client_message_id=payload.client_message_id)
            result_payload.update({"ticket_id": ticket.id, "tracking_number": business_state.tracking_number})
            if speedaf_job_id:
                result_payload["speedaf_work_order_job_id"] = speedaf_job_id
            row = db.execute(select(WebchatFastIdempotency).where(WebchatFastIdempotency.id == row_id)).scalar_one()
            mark_webchat_fast_done(db, row, response_json=result_payload)
        return JSONResponse(result_payload, status_code=200, headers=headers)

    tracking_number = _tracking_candidate(body=payload.body, context=merged_context, tracking_number=business_state.tracking_number)
    tracking_fact = _lookup_fast_tracking_fact(tracking_number=tracking_number, conversation_id=conversation_id, ticket_id=None, request_id=getattr(request.state, "request_id", None), caller_id=caller_id, country_code=payload.country_code or routing_context.country_code)
    candidate_payload = _tracking_candidate_selection_payload(tracking_fact)
    if candidate_payload:
        with db_context() as db:
            conversation = get_or_create_fast_conversation(db, tenant_key=payload.tenant_key, channel_key=payload.channel_key, session_id=payload.session_id, request=request, visitor=payload.visitor)
            append_fast_ai_message(db, conversation=conversation, reply=candidate_payload["reply"], client_message_id=payload.client_message_id, metadata={"source": "server_tracking_candidate_selection", "safe_candidates": candidate_payload.get("safe_candidates")})
            row = db.execute(select(WebchatFastIdempotency).where(WebchatFastIdempotency.id == row_id)).scalar_one()
            mark_webchat_fast_done(db, row, response_json=candidate_payload)
        return JSONResponse(candidate_payload, status_code=200, headers=headers)
    forced_payload = _tracking_fact_forced_reply_payload(tracking_number=tracking_number, result=tracking_fact)
    if forced_payload:
        _persist_tracking_fact_forced_reply(
            row_id=row_id,
            payload=payload,
            result_payload=forced_payload,
            tracking_fact_metadata=tracking_fact.metadata_payload() if tracking_fact else None,
            request=request,
        )
        return JSONResponse(forced_payload, status_code=200, headers=headers)

    tracking_fact_summary, tracking_fact_metadata, tracking_fact_evidence_present = _tracking_fact_provider_fields(tracking_fact)
    result = await generate_webchat_fast_reply(tenant_key=payload.tenant_key, channel_key=payload.channel_key, session_id=payload.session_id, body=payload.body, recent_context=merged_context, request_id=getattr(request.state, "request_id", None), tracking_fact_summary=tracking_fact_summary, tracking_fact_metadata=tracking_fact_metadata, tracking_fact_evidence_present=tracking_fact_evidence_present, market_id=routing_context.market_id)
    result_payload = result.to_response()
    with db_context() as db:
        conversation = get_or_create_fast_conversation(db, tenant_key=payload.tenant_key, channel_key=payload.channel_key, session_id=payload.session_id, request=request, visitor=payload.visitor)
        if result.ok:
            metadata = {"handoff_required": result.handoff_required, "reply_source": result.reply_source}
            if result.rag_trace:
                metadata["rag_trace"] = result.rag_trace
            if result.grounding_applied:
                metadata["grounding_applied"] = True
                metadata["grounding_source"] = result.grounding_source
            if tracking_fact_metadata:
                metadata["tracking_fact"] = tracking_fact_metadata
            append_fast_ai_message(db, conversation=conversation, reply=result.reply, client_message_id=payload.client_message_id, metadata=metadata)
        if result.ok and result.handoff_required:
            handoff_state = extract_fast_business_state(body=payload.body, context=merged_context, session_id=payload.session_id)
            if result.tracking_number:
                handoff_state = type(handoff_state)(intent=handoff_state.intent, issue_type=handoff_state.issue_type, tracking_number=result.tracking_number, fast_issue_key=f"tracking:{result.tracking_number}:intent:{handoff_state.issue_type}"[:240], missing_fields=())
            ticket = get_or_create_fast_ticket(db, conversation=conversation, business_state=handoff_state, handoff_reason=result.handoff_reason, recommended_agent_action=result.recommended_agent_action, customer_message=payload.body, routing_context=routing_context)
            speedaf_job_id = _maybe_enqueue_speedaf_work_order(db=db, ticket_id=ticket.id, conversation_id=conversation.id, business_state=handoff_state, body=payload.body, visitor=payload.visitor, handoff_reason=result.handoff_reason, recommended_action=result.recommended_agent_action)
            append_fast_system_handoff_message(db, conversation=conversation, handoff_reason=result.handoff_reason, recommended_agent_action=result.recommended_agent_action, client_message_id=payload.client_message_id)
            result_payload.update({"ticket_creation_queued": False, "ticket_id": ticket.id})
            if speedaf_job_id:
                result_payload["speedaf_work_order_job_id"] = speedaf_job_id
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
    headers.update({"Content-Type": "text/event-stream", "X-Accel-Buffering": "no", "Cache-Control": "no-store", "Vary": "Origin"})
    if not stream_settings.stream_enabled:
        if not _webchat_stream_route_forced_enabled():
            return JSONResponse({"error_code": "stream_disabled"}, status_code=503, headers=headers)
    if stream_settings.stream_require_accept and "text/event-stream" not in (request.headers.get("accept") or ""):
        return JSONResponse({"error_code": "stream_accept_required"}, status_code=406, headers=headers)
    enforce_webchat_fast_rate_limit(request, tenant_key=payload.tenant_key, session_id=payload.session_id)
    frontend_context = _context_payload(payload.recent_context)
    caller_id = _caller_id(payload.visitor)
    begin = prepare_webchat_fast_stream(tenant_key=payload.tenant_key, channel_key=payload.channel_key, session_id=payload.session_id, client_message_id=payload.client_message_id, body=payload.body, recent_context=frontend_context, request_id=getattr(request.state, "request_id", None))
    if begin.status == "processing":
        return JSONResponse({"error_code": "request_processing", "retry_after_ms": 1500}, status_code=202, headers=headers)
    if begin.status == "conflict":
        return JSONResponse({"error_code": "idempotency_key_reused_with_different_payload"}, status_code=409, headers=headers)
    if begin.status == "failed_non_retryable":
        return JSONResponse({"error_code": begin.error_code or "request_failed"}, status_code=409, headers=headers)
    if begin.status == "replay":
        generator = stream_webchat_fast_reply_events(begin=begin, tenant_key=payload.tenant_key, channel_key=payload.channel_key, session_id=payload.session_id, client_message_id=payload.client_message_id, body=payload.body, recent_context=[], visitor=payload.visitor, request_id=getattr(request.state, "request_id", None), settings=stream_settings)
        return StreamingResponse(generator, media_type="text/event-stream", headers=headers)
    if begin.row_id is None:
        return JSONResponse({"error_code": "idempotency_error", "retry_after_ms": 1500}, status_code=500, headers=headers)

    with db_context() as db:
        conversation = get_or_create_fast_conversation(db, tenant_key=payload.tenant_key, channel_key=payload.channel_key, session_id=payload.session_id, request=request, visitor=payload.visitor)
        visitor_message = append_fast_visitor_message(db, conversation=conversation, body=payload.body, client_message_id=payload.client_message_id, metadata={"source": "webchat_fast_stream"})
        merged_context = _trusted_context(build_fast_server_context(db, conversation=conversation, exclude_message_id=visitor_message.id), frontend_context)
        business_state = extract_fast_business_state(body=payload.body, context=merged_context, session_id=payload.session_id)
        update_fast_business_state(db, conversation=conversation, business_state=business_state, client_message_id=payload.client_message_id)
        conversation_id = conversation.id
        routing_context = resolve_fast_routing_context(db, country_code=payload.country_code, market_code=payload.market_code, channel_account_key=payload.channel_account_key)
        configured_rules = load_webchat_handoff_rules(db, market_id=routing_context.market_id, country_code=routing_context.country_code)


    support_payload = _support_hours_policy_payload(payload.body)
    if support_payload is not None:
        return StreamingResponse(_support_hours_policy_stream_events(row_id=begin.row_id, payload=payload, result_payload=support_payload, request=request), media_type="text/event-stream", headers=headers)

    # SERVER_OWNED_STREAM_BEFORE_OPENCLAW_SETTINGS_BEGIN
    # These deterministic server-owned responses must work even when generic OpenClaw streaming is not configured.
    server_policy = decide_server_handoff_policy(body=payload.body, recent_context=merged_context, configured_rules=configured_rules)
    if server_policy.handoff_required:
        return StreamingResponse(
            _server_policy_stream_events(
                row_id=begin.row_id,
                payload=payload,
                context_payload=merged_context,
                server_policy=server_policy,
                routing_context=routing_context,
            ),
            media_type="text/event-stream",
            headers=headers,
        )

    tracking_number = _tracking_candidate(body=payload.body, context=merged_context, tracking_number=business_state.tracking_number)
    tracking_fact = _lookup_fast_tracking_fact(
        tracking_number=tracking_number,
        conversation_id=conversation_id,
        ticket_id=None,
        request_id=getattr(request.state, "request_id", None),
        caller_id=caller_id,
        country_code=payload.country_code or routing_context.country_code,
    )
    candidate_payload = _tracking_candidate_selection_payload(tracking_fact)
    if candidate_payload:
        return StreamingResponse(
            _tracking_candidate_selection_stream_events(row_id=begin.row_id, payload=payload, result_payload=candidate_payload),
            media_type="text/event-stream",
            headers=headers,
        )

    forced_payload = _tracking_fact_forced_reply_payload(tracking_number=tracking_number, result=tracking_fact)
    if forced_payload:
        return StreamingResponse(
            _tracking_fact_forced_stream_events(
                row_id=begin.row_id,
                payload=payload,
                result_payload=forced_payload,
                tracking_fact_metadata=tracking_fact.metadata_payload() if tracking_fact else None,
                request=request,
            ),
            media_type="text/event-stream",
            headers=headers,
        )
    # SERVER_OWNED_STREAM_BEFORE_OPENCLAW_SETTINGS_END

    if not getattr(stream_settings, "is_openclaw_stream_configured", bool(getattr(stream_settings, "stream_enabled", False))):
        return JSONResponse({"error_code": "stream_upstream_not_configured"}, status_code=503, headers=headers)
    is_selected = is_stream_rollout_selected(tenant_key=payload.tenant_key, channel_key=payload.channel_key, session_id=payload.session_id, rollout_percent=getattr(stream_settings, "stream_rollout_percent", 100))
    if not is_selected and not _is_stream_canary_override_allowed(request, stream_settings):
        return JSONResponse({"error_code": "stream_not_in_rollout"}, status_code=503, headers=headers)

    tracking_fact_summary, tracking_fact_metadata, tracking_fact_evidence_present = _tracking_fact_provider_fields(tracking_fact)
    generator = stream_webchat_fast_reply_events(begin=begin, tenant_key=payload.tenant_key, channel_key=payload.channel_key, session_id=payload.session_id, client_message_id=payload.client_message_id, body=payload.body, recent_context=merged_context, visitor=payload.visitor, request_id=getattr(request.state, "request_id", None), settings=stream_settings, routing_context=routing_context, tracking_fact_summary=tracking_fact_summary, tracking_fact_metadata=tracking_fact_metadata, tracking_fact_evidence_present=tracking_fact_evidence_present)
    return StreamingResponse(generator, media_type="text/event-stream", headers=headers)
