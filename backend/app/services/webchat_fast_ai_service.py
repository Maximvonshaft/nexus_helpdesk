from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.db import SessionLocal

from .ai_runtime.openclaw_responses_provider import (
    build_fast_reply_input_text,
    build_fast_reply_instructions,
    build_fast_reply_session_key,
)
from .ai_runtime.provider_router import generate_fast_reply
from .ai_runtime.schemas import FastAIProviderRequest, FastAIProviderResult
from .ai_runtime_context import build_webchat_runtime_context
from .knowledge_grounding_service import enforce_grounded_answer
from .knowledge_prompt_service import summarize_rag_trace
from .provider_runtime.webchat_fast_dispatcher import dispatch_webchat_fast_reply
from .webchat_fast_config import get_webchat_fast_settings
from .webchat_fast_reply_metrics import record_fast_reply_metric


@dataclass(frozen=True)
class WebchatFastReplyResult:
    ok: bool
    ai_generated: bool
    reply_source: str | None
    reply: str | None
    intent: str | None
    tracking_number: str | None
    handoff_required: bool
    handoff_reason: str | None
    recommended_agent_action: str | None
    ticket_creation_queued: bool
    elapsed_ms: int
    error_code: str | None = None
    retry_after_ms: int | None = None
    rag_trace: dict[str, Any] | None = None
    grounding_applied: bool = False
    grounding_source: dict[str, Any] | None = None

    def to_response(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("recommended_agent_action", None)
        payload.pop("rag_trace", None)
        payload.pop("grounding_source", None)
        return payload


def _clip(value: str | None, limit: int) -> str:
    cleaned = (value or "").strip()
    return cleaned[:limit]


def _clean_context(recent_context: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    settings = get_webchat_fast_settings()
    items = recent_context or []
    cleaned: list[dict[str, str]] = []
    for item in items[-settings.history_turns * 2:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role not in {"customer", "visitor", "user", "ai", "assistant", "agent"}:
            continue
        normalized_role = "customer" if role in {"customer", "visitor", "user"} else "ai"
        text = _clip(str(item.get("text") or item.get("body") or ""), 500)
        if text:
            cleaned.append({"role": normalized_role, "text": text})
    return cleaned[-settings.history_turns * 2:]


def _instructions() -> str:
    return build_fast_reply_instructions()


def _input_text(
    *,
    body: str,
    recent_context: list[dict[str, str]],
    tracking_fact_summary: str | None = None,
    tracking_fact_evidence_present: bool = False,
    knowledge_context: dict[str, Any] | None = None,
) -> str:
    settings = get_webchat_fast_settings()
    return build_fast_reply_input_text(
        body=body,
        recent_context=recent_context,
        max_prompt_chars=settings.max_prompt_chars,
        tracking_fact_summary=tracking_fact_summary,
        tracking_fact_evidence_present=tracking_fact_evidence_present,
        knowledge_context=knowledge_context,
    )


def _session_key(*, tenant_key: str, session_id: str) -> str:
    return build_fast_reply_session_key(tenant_key=tenant_key, session_id=session_id)


def _result_from_provider(provider_result: FastAIProviderResult) -> WebchatFastReplyResult:
    safe_summary = provider_result.raw_payload_safe_summary or {}
    return WebchatFastReplyResult(
        ok=provider_result.ok,
        ai_generated=provider_result.ai_generated,
        reply_source=provider_result.reply_source,
        reply=provider_result.reply,
        intent=provider_result.intent,
        tracking_number=provider_result.tracking_number,
        handoff_required=provider_result.handoff_required,
        handoff_reason=provider_result.handoff_reason,
        recommended_agent_action=provider_result.recommended_agent_action,
        ticket_creation_queued=False,
        elapsed_ms=provider_result.elapsed_ms,
        error_code=provider_result.error_code,
        retry_after_ms=provider_result.retry_after_ms,
        rag_trace=safe_summary.get("rag_trace"),
        grounding_applied=bool(safe_summary.get("grounding_applied")),
        grounding_source=safe_summary.get("grounding_source"),
    )


def _runtime_context_for_request(
    *,
    tenant_key: str,
    channel_key: str,
    body: str,
    market_id: int | None,
    language: str | None,
) -> dict[str, Any] | None:
    db = SessionLocal()
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
        return None
    finally:
        db.close()


def _provider_result_with_summary(provider_result: FastAIProviderResult, safe_summary: dict[str, Any]) -> FastAIProviderResult:
    return FastAIProviderResult(**{**provider_result.__dict__, "raw_payload_safe_summary": safe_summary})


def _apply_grounding(
    *,
    provider_result: FastAIProviderResult,
    body: str,
    runtime_context: dict[str, Any] | None,
    tracking_fact_evidence_present: bool,
) -> FastAIProviderResult:
    knowledge = runtime_context.get("knowledge_context") if isinstance(runtime_context, dict) else None
    hits = knowledge.get("hits") if isinstance(knowledge, dict) else []
    decision = enforce_grounded_answer(
        query=body,
        provider_reply=provider_result.reply,
        hits=hits if isinstance(hits, list) else [],
        tracking_fact_evidence_present=tracking_fact_evidence_present,
    )
    safe_summary = dict(provider_result.raw_payload_safe_summary or {})
    if runtime_context:
        safe_summary["rag_trace"] = summarize_rag_trace(runtime_context)
    safe_summary["grounding_applied"] = decision.applied
    if decision.source:
        safe_summary["grounding_source"] = decision.source
    if not decision.applied:
        safe_summary["grounding_reason"] = decision.reason
        return _provider_result_with_summary(provider_result, safe_summary)
    return FastAIProviderResult(
        **{
            **provider_result.__dict__,
            "reply": decision.reply,
            "reply_source": f"{provider_result.reply_source or provider_result.raw_provider}:grounded_knowledge",
            "raw_payload_safe_summary": safe_summary,
            "intent": provider_result.intent or "other",
            "handoff_required": False,
            "handoff_reason": None,
            "recommended_agent_action": None,
        }
    )


async def generate_webchat_fast_reply(
    *,
    tenant_key: str,
    channel_key: str,
    session_id: str,
    body: str,
    recent_context: list[dict[str, Any]] | None,
    request_id: str | None = None,
    tracking_fact_summary: str | None = None,
    tracking_fact_metadata: dict[str, Any] | None = None,
    tracking_fact_evidence_present: bool = False,
    market_id: int | None = None,
    language: str | None = None,
) -> WebchatFastReplyResult:
    settings = get_webchat_fast_settings()
    if not settings.enabled:
        result = WebchatFastReplyResult(
            ok=False,
            ai_generated=False,
            reply_source=None,
            reply=None,
            intent=None,
            tracking_number=None,
            handoff_required=False,
            handoff_reason=None,
            recommended_agent_action=None,
            ticket_creation_queued=False,
            elapsed_ms=0,
            error_code="ai_unavailable",
            retry_after_ms=1500,
        )
        record_fast_reply_metric(status="ai_unavailable", elapsed_ms=0)
        return result

    evidence_present = bool(tracking_fact_evidence_present and tracking_fact_summary)
    runtime_context = _runtime_context_for_request(
        tenant_key=tenant_key,
        channel_key=channel_key,
        body=body,
        market_id=market_id,
        language=language,
    )
    provider_request = FastAIProviderRequest(
        tenant_key=tenant_key,
        channel_key=channel_key,
        session_id=session_id,
        body=body,
        recent_context=recent_context,
        request_id=request_id,
        tracking_fact_summary=tracking_fact_summary if evidence_present else None,
        tracking_fact_metadata=tracking_fact_metadata if evidence_present else None,
        tracking_fact_evidence_present=evidence_present,
        market_id=market_id,
        language=language,
        metadata=runtime_context,
    )
    if getattr(settings, "provider", None) == "provider_runtime":
        provider_result = await dispatch_webchat_fast_reply(request=provider_request)
    else:
        provider_result = await generate_fast_reply(
            request=provider_request,
            settings=settings,
        )

    if provider_result.ok:
        provider_result = _apply_grounding(
            provider_result=provider_result,
            body=body,
            runtime_context=runtime_context,
            tracking_fact_evidence_present=evidence_present,
        )

    status = "ok" if provider_result.ok else (provider_result.error_code or "ai_unavailable")
    record_fast_reply_metric(
        status=status,
        intent=provider_result.intent,
        handoff_required=provider_result.handoff_required,
        elapsed_ms=provider_result.elapsed_ms,
    )
    return _result_from_provider(provider_result)
