from __future__ import annotations

import logging
import re
from typing import Any, AsyncIterator
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from ..db import db_context
from ..settings import get_settings
from ..services.ai_runtime_context import build_webchat_runtime_context
from ..services.knowledge_prompt_service import summarize_rag_trace
from ..services.tracking_fact_schema import TrackingFactResult, hash_tracking_number
from ..services.tracking_fact_service import extract_tracking_number, lookup_tracking_fact
from ..services.webchat_ai_decision_runtime.policy_gate import validate_ai_decision
from ..services.webchat_ai_decision_runtime.schemas import AIDecision, AIDecisionEvidence, AIDecisionToolCall
from ..services.webchat_ai_decision_runtime.tool_executor import execute_decision_tools
from ..services.webchat_fast_ai_service import WebchatFastReplyResult, generate_webchat_fast_reply
from ..services.webchat_fast_config import WebchatFastSettings, get_webchat_fast_settings
from ..services.webchat_fast_idempotency_db import (
    WebchatFastIdempotency,
    begin_webchat_fast_idempotency,
    compute_legacy_v1_request_hash_aliases,
    compute_request_hash,
    mark_webchat_fast_done,
    mark_webchat_fast_failed,
)
from ..services.webchat_fast_output_parser import FastReplyParseError, assert_customer_visible_reply_is_safe
from ..services.webchat_fast_rate_limit import enforce_webchat_fast_rate_limit
from ..services.webchat_fast_session_service import (
    FastBusinessState,
    FastRoutingContext,
    append_fast_ai_message,
    append_fast_visitor_message,
    build_fast_server_context,
    extract_fast_business_state,
    fast_public_session_payload,
    get_or_create_fast_conversation,
    merge_fast_context,
    resolve_fast_routing_context,
    update_fast_business_state,
)
from ..services.webchat_fast_stream_service import StreamBeginOutcome, prepare_webchat_fast_stream, sse_event
from ..webchat_models import WebchatConversation

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
        "Access-Control-Allow-Headers": "Content-Type, X-Requested-With, Accept, X-Nexus-Stream-Canary",
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


def _public_tracking_reference(tracking_number: str | None) -> dict[str, Any]:
    safe_number = str(tracking_number or "").strip().upper()
    if not safe_number:
        return {"tracking_number": None, "tracking_number_hash": None, "tracking_number_suffix": None}
    return {
        "tracking_number": None,
        "tracking_number_hash": hash_tracking_number(safe_number),
        "tracking_number_suffix": safe_number[-6:],
    }


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
        "truth_trace": {
            "source": "speedaf_trusted_tracking_fact",
            "tool_status": result.tool_status,
            "fact_evidence_present": result.fact_evidence_present,
            "pii_redacted": result.pii_redacted,
            "tracking_number_hash": metadata.get("tracking_number_hash"),
            "raw_tracking_number_exposed": False,
        },
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


def _tracking_fact_evidence_trace(
    result: TrackingFactResult | None,
    *,
    tracking_number: str | None = None,
    no_answer_reason: str | None = None,
    candidate_count: int | None = None,
) -> dict[str, Any]:
    metadata = result.metadata_payload() if result is not None else {}
    safe_number = str(tracking_number or getattr(result, "tracking_number", "") or "").strip().upper()
    tracking_hash = metadata.get("tracking_number_hash") or _public_tracking_reference(safe_number).get("tracking_number_hash")
    fact_present = bool(result and result.fact_evidence_present and result.pii_redacted)
    reason = no_answer_reason or (result.failure_reason if result else None)
    trace: dict[str, Any] = {
        "retrieval": "trusted_tracking_fact",
        "source": "speedaf_trusted_tracking_fact",
        "tool_status": result.tool_status if result else "not_available",
        "fact_evidence_present": fact_present,
        "pii_redacted": bool(result.pii_redacted) if result else True,
        "tracking_number_hash": tracking_hash,
        "raw_tracking_number_exposed": False,
    }
    if reason:
        trace["no_answer_reason"] = reason
    if candidate_count is not None:
        trace["candidate_count"] = candidate_count
    return trace


def _server_no_evidence_trace(*, source: str, no_answer_reason: str | None = None) -> dict[str, Any]:
    trace: dict[str, Any] = {
        "retrieval": "no_evidence_fallback",
        "source": source,
        "fact_evidence_present": False,
        "policy_evidence_present": False,
        "raw_tracking_number_exposed": False,
    }
    if no_answer_reason:
        trace["no_answer_reason"] = no_answer_reason
    return trace


def _knowledge_no_evidence_payload(*, runtime_context: dict[str, Any] | None) -> dict[str, Any]:
    trace = summarize_rag_trace(runtime_context) if runtime_context else {
        "retrieval": "hybrid_rag_v2",
        "candidate_count": 0,
        "total_matches": 0,
        "retrieval_methods": [],
        "no_answer_reason": "runtime_context_unavailable",
        "top_hits": [],
        "evidence_pack": [],
        "injected_knowledge": [],
    }
    return {
        "ok": True,
        "ai_generated": False,
        "reply_source": "server_knowledge_no_evidence",
        "reply": "I do not have verified knowledge for this request yet. A human teammate can review it and help you further.",
        "intent": "handoff",
        "tracking_number": None,
        "handoff_required": True,
        "handoff_reason": trace.get("no_answer_reason") or "knowledge_no_evidence",
        "ticket_creation_queued": False,
        "elapsed_ms": 0,
        "evidence_trace": trace,
        "fallback_mode": "emergency_compatibility_only",
    }


def _provider_safe_fallback_payload(*, error_code: str | None, body: str | None) -> dict[str, Any]:
    zh = any("\u4e00" <= ch <= "\u9fff" for ch in (body or ""))
    reply = "助手暂时不可用，人工同事可以继续帮你核实这个请求。" if zh else "The assistant is temporarily unavailable. A human teammate can review this request."
    reason = error_code or "provider_unavailable"
    return {
        "ok": True,
        "ai_generated": False,
        "reply_source": "server_safe_fallback",
        "reply": reply,
        "intent": "handoff",
        "tracking_number": None,
        "handoff_required": True,
        "handoff_reason": reason,
        "ticket_creation_queued": False,
        "elapsed_ms": 0,
        "evidence_trace": _server_no_evidence_trace(source="server_safe_fallback", no_answer_reason=reason),
        "ai_decision_trace": {
            "schema_version": "webchat_ai_decision_v1",
            "mode": "emergency_fallback_only",
            "reply_source": "server_safe_fallback",
            "policy_gate": {"ok": True, "violations": [], "warnings": ["provider unavailable; fallback not presented as AI"], "checked_tools": []},
            "raw_tracking_number_exposed": False,
        },
    }


def _runtime_context_has_knowledge_evidence(runtime_context: dict[str, Any] | None) -> bool:
    knowledge = runtime_context.get("knowledge_context") if isinstance(runtime_context, dict) else None
    if not isinstance(knowledge, dict):
        return False
    return bool(knowledge.get("total_matches") or knowledge.get("hits") or knowledge.get("locked_facts"))


def _with_fast_public_session(db, conversation: WebchatConversation, payload: dict[str, Any]) -> dict[str, Any]:
    session_payload = fast_public_session_payload(db, conversation)
    return {**payload, **session_payload, "webchat_session": session_payload}


def _with_fast_public_session_from_request(payload: WebchatFastReplyRequest, response_payload: dict[str, Any], request: Request | None = None) -> dict[str, Any]:
    with db_context() as db:
        conversation = get_or_create_fast_conversation(
            db,
            tenant_key=payload.tenant_key,
            channel_key=payload.channel_key,
            session_id=payload.session_id,
            request=request,
            visitor=payload.visitor,
        )
        return _with_fast_public_session(db, conversation, response_payload)


def _should_attempt_fact_first_lookup(*, body: str | None, tracking_number: str | None, caller_id: str | None) -> bool:
    if tracking_number:
        return True
    if not caller_id:
        return False
    text = (body or "").lower()
    markers = (
        "track", "tracking", "parcel", "package", "shipment", "waybill", "status",
        "where is", "where's", "delivery", "查件", "查询", "物流", "包裹", "快递", "单号", "运单", "派送", "签收", "妥投",
    )
    return any(marker in text for marker in markers)


def _lookup_fast_tracking_fact(
    *,
    tracking_number: str | None,
    conversation_id: int | None,
    ticket_id: int | None,
    request_id: str | None,
    caller_id: str | None = None,
    country_code: str | None = None,
) -> TrackingFactResult | None:
    if not tracking_number and not caller_id:
        return None
    LOGGER.info(
        "webchat_fast_tracking_fact_lookup_started",
        extra={"event_payload": {"conversation_id": conversation_id, "ticket_id": ticket_id, "request_id": request_id, "tracking_number_hash": hash_tracking_number(tracking_number), "caller_id_present": bool(caller_id), "country_code": country_code}},
    )
    try:
        result = lookup_tracking_fact(
            tracking_number=tracking_number,
            caller_id=caller_id,
            country_code=country_code,
            conversation_id=conversation_id,
            ticket_id=ticket_id,
            request_id=request_id,
        )
    except Exception as exc:
        LOGGER.warning(
            "webchat_fast_tracking_fact_lookup_failed",
            extra={"event_payload": {"conversation_id": conversation_id, "ticket_id": ticket_id, "request_id": request_id, "tracking_number_hash": hash_tracking_number(tracking_number), "error_type": type(exc).__name__}},
        )
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


def _webchat_fast_runtime_context(
    *,
    tenant_key: str,
    channel_key: str,
    body: str,
    market_id: int | None,
    language: str | None = None,
) -> dict[str, Any] | None:
    with db_context() as db:
        try:
            return build_webchat_runtime_context(
                db,
                tenant_key=tenant_key,
                channel_key=channel_key,
                body=body,
                market_id=market_id,
                language=language,
            )
        except Exception:
            LOGGER.warning("webchat_fast_runtime_context_failed", exc_info=True)
            return None


def _begin_status_response(begin, headers: dict[str, str], payload: WebchatFastReplyRequest, request: Request | None = None) -> JSONResponse | None:
    if begin.kind == "replay":
        replayed = dict(begin.response_json or {})
        replayed["idempotent"] = True
        replayed = _with_fast_public_session_from_request(payload, replayed, request=request)
        return JSONResponse(replayed, status_code=200, headers=headers)
    if begin.kind == "processing":
        return JSONResponse({"error_code": "request_processing", "retry_after_ms": 1500}, status_code=202, headers=headers)
    if begin.kind == "conflict":
        return JSONResponse({"error_code": "idempotency_key_reused_with_different_payload"}, status_code=409, headers=headers)
    if begin.kind == "failed_non_retryable":
        return JSONResponse({"error_code": begin.error_code or "request_failed"}, status_code=409, headers=headers)
    return None


def _prepare_request_hashes(payload: WebchatFastReplyRequest, frontend_context: list[dict[str, str]]) -> tuple[str, tuple[str, ...]]:
    kwargs = dict(
        tenant_key=payload.tenant_key,
        channel_key=payload.channel_key,
        session_id=payload.session_id,
        client_message_id=payload.client_message_id,
        body=payload.body,
        recent_context=frontend_context,
    )
    return compute_request_hash(**kwargs), compute_legacy_v1_request_hash_aliases(**kwargs)


def _redact_tracking_number_from_public_trace(value: Any, tracking_number: str | None) -> Any:
    raw = str(tracking_number or "").strip()
    if not raw:
        return value
    suffix = "".join(ch for ch in raw.upper() if ch.isalnum())[-6:]
    replacement = f"tracking_number_ending_{suffix}" if suffix else "tracking_number_redacted"
    if isinstance(value, str):
        return re.sub(re.escape(raw), replacement, value, flags=re.IGNORECASE)
    if isinstance(value, list):
        return [_redact_tracking_number_from_public_trace(item, raw) for item in value]
    if isinstance(value, dict):
        return {key: _redact_tracking_number_from_public_trace(item, raw) for key, item in value.items()}
    return value


def _fallback_runtime_trace(runtime_context: dict[str, Any] | None, tracking_number: str | None = None) -> dict[str, Any]:
    if runtime_context:
        trace = summarize_rag_trace(runtime_context)
    else:
        trace = {
            "retrieval": "hybrid_rag_v2",
            "candidate_count": 0,
            "total_matches": 0,
            "retrieval_methods": [],
            "no_answer_reason": "runtime_context_unavailable",
            "top_hits": [],
            "evidence_pack": [],
            "injected_knowledge": [],
        }
    return _redact_tracking_number_from_public_trace(trace, tracking_number)


def _decision_for_execution(
    *,
    result: WebchatFastReplyResult,
    tracking_number: str | None,
    tracking_fact: TrackingFactResult | None,
    runtime_context: dict[str, Any] | None,
) -> AIDecision:
    tool_calls: list[AIDecisionToolCall] = []
    if (result.intent == "tracking" or tracking_number) and tracking_number:
        tool_calls.append(
            AIDecisionToolCall(
                tool_name="speedaf.order.query",
                arguments={"tracking_number_hash": hash_tracking_number(tracking_number)},
                reason="trusted_tracking_fact_required_for_live_status",
                requires_confirmation=False,
            )
        )
    if result.handoff_required:
        tool_calls.append(
            AIDecisionToolCall(
                tool_name="handoff.request.create",
                arguments={"reason": result.handoff_reason or "ai_requested_human_review", "intent": result.intent or "handoff_request"},
                reason="ai_requested_human_review_through_controlled_tool",
                requires_confirmation=False,
            )
        )
    evidence: list[AIDecisionEvidence] = []
    if tracking_fact is not None:
        metadata = tracking_fact.metadata_payload()
        evidence.append(
            AIDecisionEvidence(
                source="speedaf_trusted_tracking_fact" if tracking_fact.fact_evidence_present else "speedaf_tracking_fact_unavailable",
                evidence_type="trusted_tracking_fact",
                evidence_id=str(tracking_fact.tool_status or tracking_fact.failure_reason or "tracking_fact")[:240],
                fact_evidence_present=bool(tracking_fact.fact_evidence_present and tracking_fact.pii_redacted),
                tracking_number_hash=metadata.get("tracking_number_hash"),
                raw_tracking_number_exposed=False,
            )
        )
    rag_trace = _fallback_runtime_trace(runtime_context)
    evidence.append(
        AIDecisionEvidence(
            source="hybrid_rag_v2",
            evidence_type="knowledge_context",
            evidence_id=str(rag_trace.get("retrieval") or "hybrid_rag_v2")[:240],
            fact_evidence_present=bool(rag_trace.get("total_matches") or rag_trace.get("candidate_count") or rag_trace.get("evidence_pack")),
            raw_tracking_number_exposed=False,
        )
    )
    return AIDecision(
        customer_reply=result.reply or "A human teammate can review this request.",
        intent=result.intent or ("handoff_request" if result.handoff_required else "other"),
        confidence=0.7 if result.ai_generated else 0.0,
        risk_level="medium" if result.handoff_required or tracking_number else "low",
        next_action="request_handoff" if result.handoff_required else ("call_tool" if tool_calls else "reply"),
        handoff_required=result.handoff_required,
        handoff_reason=result.handoff_reason,
        tool_calls=tool_calls,
        evidence_used=evidence,
        safety_notes=[] if result.ai_generated else ["server emergency fallback is not presented as AI"],
    )


def _merge_ai_decision_trace(
    *,
    result_payload: dict[str, Any],
    decision: AIDecision,
    policy_summary: dict[str, Any],
    execution_summary: dict[str, Any],
    runtime_context: dict[str, Any] | None,
    tracking_number: str | None = None,
) -> None:
    trace = dict(result_payload.get("ai_decision_trace") or {})
    trace.setdefault("schema_version", "webchat_ai_decision_v1")
    trace.setdefault("mode", "gated")
    trace["decision"] = decision.safe_public_summary()
    trace["policy_gate"] = policy_summary
    trace["tool_execution"] = execution_summary
    trace["runtime_context_trace"] = _fallback_runtime_trace(runtime_context, tracking_number=tracking_number)
    trace["raw_tracking_number_exposed"] = False
    result_payload["ai_decision_trace"] = trace


async def _process_fast_reply(
    *,
    row_id: int,
    payload: WebchatFastReplyRequest,
    request: Request | None,
) -> dict[str, Any]:
    frontend_context = _context_payload(payload.recent_context)
    caller_id = _caller_id(payload.visitor)
    request_id = getattr(request.state, "request_id", None) if request is not None else None

    with db_context() as db:
        conversation = get_or_create_fast_conversation(
            db,
            tenant_key=payload.tenant_key,
            channel_key=payload.channel_key,
            session_id=payload.session_id,
            request=request,
            visitor=payload.visitor,
        )
        visitor_message = append_fast_visitor_message(db, conversation=conversation, body=payload.body, client_message_id=payload.client_message_id, metadata={"source": "webchat_fast"})
        merged_context = _trusted_context(build_fast_server_context(db, conversation=conversation, exclude_message_id=visitor_message.id), frontend_context)
        business_state = extract_fast_business_state(body=payload.body, context=merged_context, session_id=payload.session_id)
        update_fast_business_state(db, conversation=conversation, business_state=business_state, client_message_id=payload.client_message_id)
        conversation_id = conversation.id
        routing_context = resolve_fast_routing_context(db, country_code=payload.country_code, market_code=payload.market_code, channel_account_key=payload.channel_account_key)

    tracking_number = _tracking_candidate(body=payload.body, context=merged_context, tracking_number=business_state.tracking_number)
    tracking_fact = _lookup_fast_tracking_fact(
        tracking_number=tracking_number,
        conversation_id=conversation_id,
        ticket_id=None,
        request_id=request_id,
        caller_id=caller_id if _should_attempt_fact_first_lookup(body=payload.body, tracking_number=tracking_number, caller_id=caller_id) else None,
        country_code=payload.country_code or routing_context.country_code,
    )
    tracking_fact_summary, tracking_fact_metadata, tracking_fact_evidence_present = _tracking_fact_provider_fields(tracking_fact)
    runtime_context = _webchat_fast_runtime_context(
        tenant_key=payload.tenant_key,
        channel_key=payload.channel_key,
        body=payload.body,
        market_id=routing_context.market_id,
        language=None,
    )

    result = await generate_webchat_fast_reply(
        tenant_key=payload.tenant_key,
        channel_key=payload.channel_key,
        session_id=payload.session_id,
        body=payload.body,
        recent_context=merged_context,
        request_id=request_id,
        tracking_fact_summary=tracking_fact_summary,
        tracking_fact_metadata=tracking_fact_metadata,
        tracking_fact_evidence_present=tracking_fact_evidence_present,
        market_id=routing_context.market_id,
    )
    result_payload = result.to_response() if result.ok else _provider_safe_fallback_payload(error_code=result.error_code, body=payload.body)
    result_payload.update(_public_tracking_reference(result.tracking_number or tracking_number))
    if tracking_fact is not None:
        result_payload["tracking_fact"] = _tracking_fact_public_payload(tracking_fact)
    if tracking_fact is not None and tracking_fact.fact_evidence_present:
        result_payload["evidence_trace"] = _tracking_fact_evidence_trace(tracking_fact, tracking_number=tracking_number)
    else:
        result_payload.setdefault("evidence_trace", _fallback_runtime_trace(runtime_context, tracking_number=tracking_number))

    with db_context() as db:
        conversation = get_or_create_fast_conversation(
            db,
            tenant_key=payload.tenant_key,
            channel_key=payload.channel_key,
            session_id=payload.session_id,
            request=request,
            visitor=payload.visitor,
        )
        business_state = extract_fast_business_state(body=payload.body, context=merged_context, session_id=payload.session_id)
        if tracking_number:
            business_state = FastBusinessState(
                intent=business_state.intent,
                issue_type=business_state.issue_type,
                tracking_number=tracking_number,
                fast_issue_key=f"tracking:{tracking_number}:intent:{business_state.issue_type}"[:240],
                missing_fields=business_state.missing_fields,
            )
        decision = _decision_for_execution(result=result if result.ok else WebchatFastReplyResult(**{**result.__dict__, "reply": result_payload.get("reply"), "handoff_required": bool(result_payload.get("handoff_required")), "handoff_reason": result_payload.get("handoff_reason"), "intent": result_payload.get("intent")}), tracking_number=tracking_number, tracking_fact=tracking_fact, runtime_context=runtime_context)
        policy = validate_ai_decision(decision, tracking_fact_metadata=tracking_fact_metadata if tracking_fact_evidence_present else None, tracking_number=tracking_number)
        execution = execute_decision_tools(
            db,
            decision=decision,
            policy_result=policy,
            conversation=conversation,
            business_state=business_state,
            routing_context=routing_context,
            tenant_key=payload.tenant_key,
            channel_key=payload.channel_key,
            session_id=payload.session_id,
            client_message_id=payload.client_message_id,
            customer_message=payload.body,
            request_id=request_id,
        )
        if not policy.ok:
            result_payload = _provider_safe_fallback_payload(error_code="ai_decision_policy_blocked", body=payload.body)
        else:
            for record in execution.records:
                if record.tool_name == "handoff.request.create" and record.status == "executed":
                    result_payload.update(record.result)
                    result_payload["ticket_creation_queued"] = False
        _merge_ai_decision_trace(
            result_payload=result_payload,
            decision=decision,
            policy_summary=policy.safe_summary(),
            execution_summary=execution.safe_summary(),
            runtime_context=runtime_context,
            tracking_number=tracking_number,
        )
        metadata = {
            "handoff_required": bool(result_payload.get("handoff_required")),
            "reply_source": result_payload.get("reply_source"),
            "ai_decision_trace": result_payload.get("ai_decision_trace"),
        }
        if result.rag_trace:
            metadata["rag_trace"] = result.rag_trace
        if result.grounding_applied:
            metadata["grounding_applied"] = True
            if result.grounding_source:
                metadata["grounding_source"] = result.grounding_source
            if result.grounding_reason:
                metadata["grounding_reason"] = result.grounding_reason
        if tracking_fact_metadata:
            metadata["tracking_fact"] = tracking_fact_metadata
        if result_payload.get("reply"):
            append_fast_ai_message(db, conversation=conversation, reply=result_payload.get("reply"), client_message_id=payload.client_message_id, metadata=metadata)
        row = db.execute(select(WebchatFastIdempotency).where(WebchatFastIdempotency.id == row_id)).scalar_one()
        mark_webchat_fast_done(db, row, response_json=result_payload)
        return _with_fast_public_session(db, conversation, result_payload)


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
    frontend_context = _context_payload(payload.recent_context)
    request_hash, request_hash_aliases = _prepare_request_hashes(payload, frontend_context)
    with db_context() as db:
        begin = begin_webchat_fast_idempotency(
            db,
            tenant_key=payload.tenant_key,
            session_id=payload.session_id,
            client_message_id=payload.client_message_id,
            request_hash=request_hash,
            request_hash_aliases=request_hash_aliases,
            owner_request_id=getattr(request.state, "request_id", None),
        )
        row_id = begin.row.id if begin.row is not None else None
    status_response = _begin_status_response(begin, headers, payload, request=request)
    if status_response is not None:
        return status_response
    if row_id is None:
        return JSONResponse({"error_code": "idempotency_error", "retry_after_ms": 1500}, status_code=500, headers=headers)
    try:
        public_payload = await _process_fast_reply(row_id=row_id, payload=payload, request=request)
        return JSONResponse(public_payload, status_code=200, headers=headers)
    except Exception:
        with db_context() as db:
            row = db.execute(select(WebchatFastIdempotency).where(WebchatFastIdempotency.id == row_id)).scalar_one_or_none()
            if row is not None:
                mark_webchat_fast_failed(db, row, error_code="webchat_fast_internal_error")
        LOGGER.exception("webchat_fast_reply_failed")
        return JSONResponse({"error_code": "webchat_fast_internal_error", "retry_after_ms": 1500}, status_code=500, headers=headers)


def _stream_begin_status_response(begin: StreamBeginOutcome, headers: dict[str, str]) -> JSONResponse | None:
    if begin.status == "processing":
        return JSONResponse({"error_code": "request_processing", "retry_after_ms": 1500}, status_code=202, headers=headers)
    if begin.status == "conflict":
        return JSONResponse({"error_code": "idempotency_key_reused_with_different_payload"}, status_code=409, headers=headers)
    if begin.status == "failed_non_retryable":
        return JSONResponse({"error_code": begin.error_code or "request_failed"}, status_code=409, headers=headers)
    return None


async def _stream_replay_events(*, payload: WebchatFastReplyRequest, stored: dict[str, Any]) -> AsyncIterator[str]:
    final = {k: v for k, v in dict(stored).items() if k != "reply"}
    final["replayed"] = True

    replay_reply: str | None = None
    replay_error: str | None = None
    raw_reply = stored.get("reply")
    if raw_reply is not None:
        if not isinstance(raw_reply, str):
            replay_error = "ai_invalid_output"
        else:
            cleaned = raw_reply.strip()
            if cleaned:
                try:
                    assert_customer_visible_reply_is_safe(cleaned)
                    replay_reply = cleaned
                except FastReplyParseError:
                    replay_error = "ai_invalid_output"

    yield sse_event("replay", {"replayed": True})
    if replay_error:
        yield sse_event("error", {"error_code": replay_error, "replayed": True})
        return

    with db_context() as db:
        conversation = get_or_create_fast_conversation(db, tenant_key=payload.tenant_key, channel_key=payload.channel_key, session_id=payload.session_id)
        session_payload = fast_public_session_payload(db, conversation)
        final.update(session_payload)
        final["webchat_session"] = session_payload

    yield sse_event("final", final)
    if replay_reply:
        yield sse_event("reply_delta", {"text": replay_reply})


async def _stream_process_events(*, row_id: int, payload: WebchatFastReplyRequest, request: Request | None) -> AsyncIterator[str]:
    try:
        yield sse_event("meta", {"replayed": False, "stream_version": "V3.ai_decision_runtime", "decision_runtime": "webchat_ai_decision_v1"})
        public_payload = await _process_fast_reply(row_id=row_id, payload=payload, request=request)
        reply = public_payload.get("reply") or ""
        final = {k: v for k, v in public_payload.items() if k != "reply"}
        yield sse_event("final", final)
        if reply:
            yield sse_event("reply_delta", {"text": reply})
    except Exception:
        with db_context() as db:
            row = db.execute(select(WebchatFastIdempotency).where(WebchatFastIdempotency.id == row_id)).scalar_one_or_none()
            if row is not None:
                mark_webchat_fast_failed(db, row, error_code="stream_internal_error")
        LOGGER.exception("webchat_fast_stream_failed")
        yield sse_event("error", {"error_code": "stream_internal_error", "retry_after_ms": 1500})


@router.post("/fast-reply/stream")
async def webchat_fast_reply_stream(payload: WebchatFastReplyRequest, request: Request) -> Response:
    stream_settings = get_webchat_fast_settings()
    headers = _public_cors_headers(request)
    headers.update({"Content-Type": "text/event-stream", "X-Accel-Buffering": "no", "Cache-Control": "no-store", "Vary": "Origin"})
    if not stream_settings.stream_enabled:
        return JSONResponse({"error_code": "stream_disabled"}, status_code=503, headers=headers)
    if stream_settings.stream_require_accept and "text/event-stream" not in (request.headers.get("accept") or ""):
        return JSONResponse({"error_code": "stream_accept_required"}, status_code=406, headers=headers)
    enforce_webchat_fast_rate_limit(request, tenant_key=payload.tenant_key, session_id=payload.session_id)
    frontend_context = _context_payload(payload.recent_context)
    begin = prepare_webchat_fast_stream(
        tenant_key=payload.tenant_key,
        channel_key=payload.channel_key,
        session_id=payload.session_id,
        client_message_id=payload.client_message_id,
        body=payload.body,
        recent_context=frontend_context,
        request_id=getattr(request.state, "request_id", None),
    )
    status_response = _stream_begin_status_response(begin, headers)
    if status_response is not None:
        return status_response
    if begin.status == "replay":
        return StreamingResponse(_stream_replay_events(payload=payload, stored=dict(begin.response_json or {})), media_type="text/event-stream", headers=headers)
    if begin.row_id is None:
        return JSONResponse({"error_code": "idempotency_error", "retry_after_ms": 1500}, status_code=500, headers=headers)
    return StreamingResponse(_stream_process_events(row_id=begin.row_id, payload=payload, request=request), media_type="text/event-stream", headers=headers)
