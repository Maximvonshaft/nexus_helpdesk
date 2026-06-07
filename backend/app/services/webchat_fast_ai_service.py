from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from app.db import SessionLocal
from app.settings import get_settings

from .ai_runtime.openclaw_responses_provider import (
    build_fast_reply_input_text,
    build_fast_reply_instructions,
    build_fast_reply_session_key,
)
from .ai_runtime.provider_router import generate_fast_reply
from .ai_runtime.schemas import FastAIProviderRequest, FastAIProviderResult
from .ai_runtime_context import build_webchat_runtime_context
from .domain_intelligence.webchat_shadow_bridge import build_webchat_domain_shadow_trace
from .knowledge_grounding_service import enforce_grounded_answer, is_explicit_handoff_or_business_action, select_approved_direct_answer_override, select_trusted_direct_answer_evidence
from .knowledge_prompt_service import summarize_rag_trace
from .provider_runtime.webchat_fast_dispatcher import dispatch_webchat_fast_reply
from .webchat_ai_decision_runtime.service import decision_from_provider_result, validate_and_trace_decision
from .webchat_fast_config import get_webchat_fast_settings
from .webchat_fast_output_parser import FastReplyParseError
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
    grounding_reason: str | None = None
    ai_decision_trace: dict[str, Any] | None = None

    def to_response(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("recommended_agent_action", None)
        rag_trace = payload.pop("rag_trace", None)
        if rag_trace:
            payload["evidence_trace"] = rag_trace
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


def _result_from_provider(
    provider_result: FastAIProviderResult,
    *,
    tracking_fact_metadata: dict[str, Any] | None = None,
    tracking_number: str | None = None,
    runtime_context: dict[str, Any] | None = None,
    tenant_key: str | None = None,
    channel_key: str | None = None,
    session_id: str | None = None,
    request_id: str | None = None,
    body: str | None = None,
) -> WebchatFastReplyResult:
    safe_summary = provider_result.raw_payload_safe_summary or {}
    grounded_reply_source = str(provider_result.reply_source or "").endswith(":grounded_knowledge")
    grounding_applied = bool(safe_summary.get("grounding_applied")) or grounded_reply_source
    ai_decision_trace = safe_summary.get("ai_decision_trace") if isinstance(safe_summary.get("ai_decision_trace"), dict) else None
    intent = provider_result.intent
    handoff_required = provider_result.handoff_required
    handoff_reason = provider_result.handoff_reason
    reply = provider_result.reply

    if provider_result.ok and provider_result.reply:
        try:
            decision = decision_from_provider_result(
                provider_result,
                tracking_fact_metadata=tracking_fact_metadata,
                tracking_number=tracking_number or provider_result.tracking_number,
                runtime_context=runtime_context,
                request_body=body,
            )
            policy, ai_decision_trace = validate_and_trace_decision(
                decision=decision,
                tracking_fact_metadata=tracking_fact_metadata,
                tracking_number=tracking_number or provider_result.tracking_number,
                reply_source=provider_result.reply_source,
                runtime_context=runtime_context,
                mode="gated",
                request_id=request_id,
                tenant_key=tenant_key,
                channel_key=channel_key,
                session_id=session_id,
            )
            if not policy.ok:
                return WebchatFastReplyResult(
                    ok=False,
                    ai_generated=False,
                    reply_source=provider_result.reply_source,
                    reply=None,
                    intent=decision.intent,
                    tracking_number=None,
                    handoff_required=False,
                    handoff_reason=None,
                    recommended_agent_action=None,
                    ticket_creation_queued=False,
                    elapsed_ms=provider_result.elapsed_ms,
                    error_code="ai_decision_policy_blocked",
                    retry_after_ms=1500,
                    rag_trace=safe_summary.get("rag_trace"),
                    grounding_applied=grounding_applied,
                    grounding_source=safe_summary.get("grounding_source"),
                    grounding_reason=safe_summary.get("grounding_reason"),
                    ai_decision_trace=ai_decision_trace,
                )
            intent = decision.intent
            handoff_required = decision.handoff_required
            handoff_reason = decision.handoff_reason
            reply = decision.customer_reply
        except FastReplyParseError:
            return WebchatFastReplyResult(
                ok=False,
                ai_generated=False,
                reply_source=provider_result.reply_source,
                reply=None,
                intent=provider_result.intent,
                tracking_number=None,
                handoff_required=False,
                handoff_reason=None,
                recommended_agent_action=None,
                ticket_creation_queued=False,
                elapsed_ms=provider_result.elapsed_ms,
                error_code="ai_decision_invalid_output",
                retry_after_ms=1500,
                rag_trace=safe_summary.get("rag_trace"),
                grounding_applied=grounding_applied,
                grounding_source=safe_summary.get("grounding_source"),
                grounding_reason=safe_summary.get("grounding_reason"),
                ai_decision_trace=ai_decision_trace,
            )

    return WebchatFastReplyResult(
        ok=provider_result.ok,
        ai_generated=provider_result.ai_generated,
        reply_source=provider_result.reply_source,
        reply=reply,
        intent=intent,
        tracking_number=provider_result.tracking_number,
        handoff_required=handoff_required,
        handoff_reason=handoff_reason,
        recommended_agent_action=provider_result.recommended_agent_action,
        ticket_creation_queued=False,
        elapsed_ms=provider_result.elapsed_ms,
        error_code=provider_result.error_code,
        retry_after_ms=provider_result.retry_after_ms,
        rag_trace=safe_summary.get("rag_trace"),
        grounding_applied=grounding_applied,
        grounding_source=safe_summary.get("grounding_source"),
        grounding_reason=safe_summary.get("grounding_reason"),
        ai_decision_trace=ai_decision_trace,
    )


def _runtime_context_for_request(
    *,
    tenant_key: str,
    channel_key: str,
    body: str,
    market_id: int | None,
    language: str | None,
    tracking_number: str | None = None,
    tracking_fact_evidence_present: bool | None = None,
) -> dict[str, Any] | None:
    db = SessionLocal()
    try:
        runtime_context = build_webchat_runtime_context(
            db,
            tenant_key=tenant_key,
            channel_key=channel_key,
            body=body,
            market_id=market_id,
            language=language,
            tracking_number=tracking_number,
            tracking_fact_evidence_present=tracking_fact_evidence_present,
        )
        return _attach_domain_shadow_trace(
            runtime_context,
            body=body,
            tenant_key=tenant_key,
            channel_key=channel_key,
            market_id=market_id,
            language=language,
        )
    except Exception:
        return None
    finally:
        db.close()


def _attach_domain_shadow_trace(
    runtime_context: dict[str, Any] | None,
    *,
    body: str,
    tenant_key: str,
    channel_key: str,
    market_id: int | None,
    language: str | None,
) -> dict[str, Any] | None:
    if not isinstance(runtime_context, dict):
        return runtime_context
    try:
        trace = build_webchat_domain_shadow_trace(
            body=body,
            tenant_key=tenant_key,
            channel_key=channel_key,
            market_id=market_id,
            language=language,
        )
    except Exception:
        trace = None
    if not trace:
        return runtime_context
    return {**runtime_context, "domain_intelligence_trace": trace}


def _provider_result_with_summary(provider_result: FastAIProviderResult, safe_summary: dict[str, Any]) -> FastAIProviderResult:
    return FastAIProviderResult(**{**provider_result.__dict__, "raw_payload_safe_summary": safe_summary})


def _is_already_grounded_provider_result(provider_result: FastAIProviderResult) -> bool:
    safe_summary = provider_result.raw_payload_safe_summary or {}
    return bool(safe_summary.get("grounding_applied")) or str(provider_result.reply_source or "").endswith(":grounded_knowledge")


def _deterministic_direct_answer_enabled() -> bool:
    return get_settings().webchat_knowledge_reply_mode == "deterministic_direct_answer"


def _knowledge_context(runtime_context: dict[str, Any] | None) -> dict[str, Any]:
    knowledge = runtime_context.get("knowledge_context") if isinstance(runtime_context, dict) else None
    return knowledge if isinstance(knowledge, dict) else {}



def _direct_answer_repair_blocked_by_tracking_query(body: str | None) -> bool:
    text = (body or "").strip().lower()
    if not text:
        return False
    if re.search(r"\b(?=[a-z0-9]{8,30}\b)(?=[a-z0-9]*\d)[a-z0-9]+\b", text, re.I):
        return True
    markers = (
        "where is", "where's", "tracking", "track", "parcel", "package", "shipment",
        "waybill", "status", "delivery status", "delivered", "in transit",
        "out for delivery", "customs", "returned", "failed delivery",
        "在哪里", "到哪里", "查件", "查询", "物流", "包裹", "快递", "单号", "运单",
        "派送", "签收", "妥投", "运输中", "清关", "退回", "状态",
    )
    return any(marker in text for marker in markers)


def _provider_unavailable_trusted_direct_answer_result(
    *,
    provider_result: FastAIProviderResult,
    body: str,
    runtime_context: dict[str, Any] | None,
    tracking_fact_evidence_present: bool,
) -> WebchatFastReplyResult | None:
    if tracking_fact_evidence_present:
        return None
    if is_explicit_handoff_or_business_action(body):
        return None
    if _direct_answer_repair_blocked_by_tracking_query(body):
        return None

    decision = select_trusted_direct_answer_evidence(
        _knowledge_context(runtime_context),
        query=body,
        tracking_fact_evidence_present=False,
    )
    if not decision.applied or not decision.reply:
        return None

    source = decision.source if isinstance(decision.source, dict) else {}
    reply_source = provider_result.raw_provider or provider_result.reply_source or "provider_runtime"
    return WebchatFastReplyResult(
        ok=True,
        ai_generated=True,
        reply_source=reply_source,
        reply=decision.reply,
        intent="other",
        tracking_number=None,
        handoff_required=False,
        handoff_reason=None,
        recommended_agent_action=None,
        ticket_creation_queued=False,
        elapsed_ms=provider_result.elapsed_ms,
        rag_trace=summarize_rag_trace(runtime_context),
        grounding_applied=True,
        grounding_source=source,
        grounding_reason="trusted_kb_direct_answer_provider_unavailable_repair",
        ai_decision_trace={
            "schema_version": "webchat_ai_decision_v1",
            "mode": "trusted_kb_direct_answer_provider_unavailable_repair",
            "reply_source": reply_source,
            "repair_applied": True,
            "repair_reason": "trusted_kb_direct_answer_provider_unavailable_repair",
            "decision": {
                "intent": "general_support",
                "risk_level": "low",
                "next_action": "reply",
                "handoff_required": False,
                "handoff_reason": None,
                "tool_calls": [],
                "evidence_used": [
                    {
                        "source": "hybrid_rag_v2",
                        "evidence_type": "knowledge_context",
                        "evidence_id": str(source.get("item_key") or source.get("title") or "trusted_direct_answer")[:240],
                        "fact_evidence_present": True,
                        "raw_tracking_number_exposed": False,
                    }
                ],
                "safety_notes": ["provider unavailable path repaired by trusted KB direct_answer"],
            },
            "policy_gate": {"ok": True, "violations": [], "warnings": [], "checked_tools": []},
            "raw_tracking_number_exposed": False,
        },
    )


def _pre_provider_locked_fact_direct_answer_result(
    *,
    body: str,
    runtime_context: dict[str, Any] | None,
    tracking_fact_evidence_present: bool,
) -> WebchatFastReplyResult | None:
    knowledge_context = _knowledge_context(runtime_context)
    locked_facts = knowledge_context.get("locked_facts")
    if not isinstance(locked_facts, list) or not locked_facts:
        return None

    grounding_decision = select_approved_direct_answer_override(
        query=body,
        provider_output=None,
        knowledge_context=knowledge_context,
        tracking_fact_evidence_present=tracking_fact_evidence_present,
    )
    if not grounding_decision.applied or not grounding_decision.reply:
        return None

    source = grounding_decision.source or {}
    if not isinstance(source, dict):
        source = {}

    return WebchatFastReplyResult(
        ok=True,
        ai_generated=True,
        reply_source="provider_runtime",
        reply=grounding_decision.reply,
        intent="other",
        tracking_number=None,
        handoff_required=False,
        handoff_reason=None,
        recommended_agent_action=None,
        ticket_creation_queued=False,
        elapsed_ms=0,
        rag_trace=summarize_rag_trace(runtime_context),
        grounding_applied=True,
        grounding_source=source,
        grounding_reason="pre_provider_locked_fact_direct_answer",
        ai_decision_trace={
            "schema_version": "webchat_ai_decision_v1",
            "mode": "trusted_kb_direct_answer_pre_provider",
            "reply_source": "provider_runtime",
            "decision": {
                "intent": "general_support",
                "risk_level": "low",
                "next_action": "reply",
                "handoff_required": False,
                "tool_calls": [],
                "evidence_used": [{"source": "hybrid_rag_v2", "evidence_type": "locked_fact", "fact_evidence_present": True}],
                "safety_notes": ["trusted KB direct_answer returned through WebChat Fast AI runtime"],
            },
            "policy_gate": {"ok": True, "violations": [], "warnings": [], "checked_tools": []},
            "raw_tracking_number_exposed": False,
        },
    )


def _pre_provider_no_evidence_result(
    *,
    runtime_context: dict[str, Any] | None,
    tracking_fact_evidence_present: bool,
) -> WebchatFastReplyResult | None:
    # Transitional note: server_knowledge_no_evidence is no longer a normal
    # customer-service brain. Low-signal/no-evidence input must reach the AI
    # decision runtime. Provider-unavailable emergency fallback remains in the
    # API layer and is explicitly marked as server_safe_fallback.
    return None


def _apply_grounding(
    *,
    provider_result: FastAIProviderResult,
    body: str,
    runtime_context: dict[str, Any] | None,
    tracking_fact_evidence_present: bool,
) -> FastAIProviderResult:
    safe_summary = dict(provider_result.raw_payload_safe_summary or {})
    if runtime_context:
        safe_summary.setdefault("rag_trace", summarize_rag_trace(runtime_context))
    if _is_already_grounded_provider_result(provider_result):
        safe_summary["grounding_applied"] = True
        safe_summary.setdefault("grounding_reason", "provider_runtime_grounded_knowledge")
        return _provider_result_with_summary(provider_result, safe_summary)
    if not _deterministic_direct_answer_enabled():
        knowledge = runtime_context.get("knowledge_context") if isinstance(runtime_context, dict) else None
        if isinstance(knowledge, dict) and knowledge.get("locked_facts"):
            safe_summary.setdefault("grounding_applied", False)
            safe_summary.setdefault("grounding_reason", "ai_grounded_provider_result_not_overridden")
            safe_summary.setdefault("locked_fact_ids", [
                str(fact.get("item_key"))
                for fact in knowledge.get("locked_facts", [])
                if isinstance(fact, dict) and fact.get("item_key")
            ])
        return _provider_result_with_summary(provider_result, safe_summary)

    knowledge = runtime_context.get("knowledge_context") if isinstance(runtime_context, dict) else None
    hits = knowledge.get("hits") if isinstance(knowledge, dict) else []
    decision = enforce_grounded_answer(
        query=body,
        provider_reply=provider_result.reply,
        hits=hits if isinstance(hits, list) else [],
        tracking_fact_evidence_present=tracking_fact_evidence_present,
    )
    safe_summary["grounding_applied"] = decision.applied
    safe_summary["grounding_reason"] = decision.reason
    if decision.source:
        safe_summary["grounding_source"] = decision.source
    if not decision.applied:
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
        tracking_number=tracking_fact_metadata.get("tracking_number") if isinstance(tracking_fact_metadata, dict) else None,
        tracking_fact_evidence_present=evidence_present,
    )
    pre_provider_direct_answer = _pre_provider_locked_fact_direct_answer_result(
        body=body,
        runtime_context=runtime_context,
        tracking_fact_evidence_present=evidence_present,
    )
    if pre_provider_direct_answer is not None:
        record_fast_reply_metric(
            status="ok",
            intent=pre_provider_direct_answer.intent,
            handoff_required=pre_provider_direct_answer.handoff_required,
            elapsed_ms=pre_provider_direct_answer.elapsed_ms,
        )
        return pre_provider_direct_answer
    no_evidence_result = _pre_provider_no_evidence_result(
        runtime_context=runtime_context,
        tracking_fact_evidence_present=evidence_present,
    )
    if no_evidence_result is not None:
        record_fast_reply_metric(
            status="ok",
            intent=no_evidence_result.intent,
            handoff_required=no_evidence_result.handoff_required,
            elapsed_ms=no_evidence_result.elapsed_ms,
        )
        return no_evidence_result

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

    if not provider_result.ok:
        provider_unavailable_direct_answer = _provider_unavailable_trusted_direct_answer_result(
            provider_result=provider_result,
            body=body,
            runtime_context=runtime_context,
            tracking_fact_evidence_present=evidence_present,
        )
        if provider_unavailable_direct_answer is not None:
            record_fast_reply_metric(
                status="ok",
                intent=provider_unavailable_direct_answer.intent,
                handoff_required=provider_unavailable_direct_answer.handoff_required,
                elapsed_ms=provider_unavailable_direct_answer.elapsed_ms,
            )
            return provider_unavailable_direct_answer

    if provider_result.ok:
        provider_result = _apply_grounding(
            provider_result=provider_result,
            body=body,
            runtime_context=runtime_context,
            tracking_fact_evidence_present=evidence_present,
        )

    result = _result_from_provider(
        provider_result,
        tracking_fact_metadata=tracking_fact_metadata if evidence_present else None,
        tracking_number=tracking_fact_metadata.get("tracking_number") if isinstance(tracking_fact_metadata, dict) else provider_result.tracking_number,
        runtime_context=runtime_context,
        tenant_key=tenant_key,
        channel_key=channel_key,
        session_id=session_id,
        request_id=request_id,
        body=body,
    )
    status = "ok" if result.ok else (result.error_code or provider_result.error_code or "ai_unavailable")
    record_fast_reply_metric(
        status=status,
        intent=result.intent,
        handoff_required=result.handoff_required,
        elapsed_ms=result.elapsed_ms,
    )
    return result
