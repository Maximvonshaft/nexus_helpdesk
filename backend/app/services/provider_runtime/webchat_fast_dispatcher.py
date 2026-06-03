from __future__ import annotations

import logging

from app.db import SessionLocal
from app.settings import get_settings

from ..ai_runtime.schemas import FastAIProviderRequest, FastAIProviderResult
from ..ai_runtime_context import build_webchat_runtime_context
from ..knowledge_grounding_service import (
    enforce_grounded_answer,
    is_explicit_handoff_or_business_action,
    select_approved_direct_answer_override,
    select_trusted_direct_answer_evidence,
)
from ..knowledge_prompt_service import summarize_rag_trace
from .output_contracts import OutputContracts
from .router import ProviderRuntimeRouter
from .schemas import ProviderRequest

logger = logging.getLogger(__name__)


def _fallback_runtime_context(request: FastAIProviderRequest) -> dict:
    return {
        "context_version": "nexus_webchat_runtime_context_v1",
        "tenant_key": request.tenant_key,
        "metadata_filters": {
            "market_id": request.market_id,
            "channel": request.channel_key,
            "language": request.language,
            "audience_scope": "customer",
        },
        "persona_context": None,
        "knowledge_context": {"retrieval": "unavailable", "total_matches": 0, "locked_facts": [], "hits": []},
        "safety_policy": {
            "knowledge_scope": "policy_sop_faq_only",
            "locked_facts_contract": "Use locked_facts as authoritative facts when present; never change numbers, country, service type, or policy boundaries.",
            "tracking_truth_boundary": "Parcel live status requires tracking_fact_evidence_present=true and trusted tracking_fact_summary.",
        },
    }


def build_webchat_fast_provider_request(request: FastAIProviderRequest, *, metadata: dict | None = None) -> ProviderRequest:
    safe_metadata = dict(metadata or {})
    if request.metadata:
        safe_metadata.update(request.metadata)
    if request.tracking_fact_evidence_present and request.tracking_fact_metadata:
        safe_metadata["tracking_fact_metadata"] = request.tracking_fact_metadata
    return ProviderRequest(
        request_id=request.request_id or "req_unknown",
        tenant_id=request.tenant_key,
        tenant_key=request.tenant_key,
        channel_key=request.channel_key,
        session_id=request.session_id,
        scenario="webchat_fast_reply",
        body=request.body,
        recent_context=request.recent_context,
        tracking_fact_summary=request.tracking_fact_summary,
        tracking_fact_evidence_present=request.tracking_fact_evidence_present,
        output_contract="speedaf_webchat_fast_reply_v1",
        timeout_ms=10000,
        metadata=safe_metadata,
    )


def _grounded_intent(value: object) -> str:
    intent = str(value or "").strip()
    if intent in {"tracking", "tracking_missing_number", "tracking_unresolved"}:
        return "other"
    return intent or "other"


def _knowledge_context(runtime_context: dict | None) -> dict:
    knowledge = ((runtime_context or {}).get("knowledge_context") or {})
    return knowledge if isinstance(knowledge, dict) else {}


def _knowledge_reply_mode() -> str:
    return get_settings().webchat_knowledge_reply_mode


def _ai_grounded_summary(output: dict, knowledge_context: dict) -> dict:
    reply = output.get("customer_reply") or output.get("reply")
    validation = OutputContracts.locked_fact_validation(reply, knowledge_context)
    summary = {
        "grounding_validation": validation["status"],
        "grounded_by_ai": validation["status"] == "pass",
        "grounding_applied": validation["status"] == "pass",
        "locked_fact_ids": validation.get("locked_fact_ids") or [],
    }
    if validation.get("source"):
        summary["grounding_source"] = validation["source"]
    if validation["status"] == "pass":
        summary["grounding_reason"] = "locked_fact_ai_grounded"
    return summary


def _ai_grounded_validation_failed(output: dict, knowledge_context: dict) -> bool:
    reply = output.get("customer_reply") or output.get("reply")
    return OutputContracts.locked_fact_validation(reply, knowledge_context)["status"] == "fail"


def _ai_decision_summary_from_output(output: dict) -> dict | None:
    if not isinstance(output, dict):
        return None
    reply = output.get("customer_reply") or output.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        return None
    handoff_required = bool(output.get("handoff_required", False))
    return {
        "customer_reply": reply,
        "intent": output.get("intent") or "other",
        "confidence": output.get("confidence", 0.7),
        "risk_level": output.get("risk_level") or ("medium" if handoff_required else "low"),
        "next_action": output.get("next_action") or ("request_handoff" if handoff_required else "reply"),
        "handoff_required": handoff_required,
        "handoff_reason": output.get("handoff_reason"),
        "tool_calls": output.get("tool_calls") if isinstance(output.get("tool_calls"), list) else [],
        "evidence_used": output.get("evidence_used") if isinstance(output.get("evidence_used"), list) else [],
        "safety_notes": output.get("safety_notes") if isinstance(output.get("safety_notes"), list) else [],
    }


def _output_requests_handoff_or_fallback(output: dict) -> bool:
    reply = str(output.get("customer_reply") or output.get("reply") or "").strip().lower()
    tool_calls = output.get("tool_calls") if isinstance(output.get("tool_calls"), list) else []
    return bool(
        output.get("handoff_required") is True
        or str(output.get("intent") or "").strip().lower() in {"handoff", "handoff_request"}
        or str(output.get("next_action") or "").strip().lower() in {"handoff", "request_handoff"}
        or any(isinstance(call, dict) and call.get("tool_name") == "handoff.request.create" for call in tool_calls)
        or "human teammate" in reply
        or "assistant is temporarily unavailable" in reply
        or "人工" in reply
        or "暂时不可用" in reply
    )


def _repair_output_with_trusted_direct_answer(
    *,
    output: dict,
    request: FastAIProviderRequest,
    knowledge_context: dict,
) -> tuple[dict, dict | None]:
    if request.tracking_fact_evidence_present or is_explicit_handoff_or_business_action(request.body):
        return output, None
    if not _output_requests_handoff_or_fallback(output):
        return output, None
    decision = select_trusted_direct_answer_evidence(
        knowledge_context,
        tracking_fact_evidence_present=request.tracking_fact_evidence_present,
    )
    if not decision.applied or not decision.reply:
        return output, None
    repaired = {
        **output,
        "customer_reply": decision.reply,
        "reply": decision.reply,
        "intent": "other",
        "tracking_number": None,
        "handoff_required": False,
        "handoff_reason": None,
        "recommended_agent_action": None,
        "ticket_should_create": False,
        "tool_calls": [],
        "next_action": "reply",
    }
    repair_trace = {
        "repair_applied": True,
        "repair_reason": "trusted_kb_direct_answer_policy_repair",
        "grounding_reason": "trusted_kb_direct_answer_policy_repair",
        "grounding_applied": True,
        "grounding_source": decision.source,
    }
    return repaired, repair_trace


def _pre_provider_direct_answer_result(
    *,
    request: FastAIProviderRequest,
    runtime_context: dict | None,
) -> FastAIProviderResult | None:
    if _knowledge_reply_mode() != "deterministic_direct_answer":
        return None
    if request.tracking_fact_evidence_present:
        return None

    knowledge_context = _knowledge_context(runtime_context)
    grounding_decision = select_approved_direct_answer_override(
        query=request.body,
        provider_output=None,
        knowledge_context=knowledge_context,
        tracking_fact_evidence_present=request.tracking_fact_evidence_present,
    )
    if not grounding_decision.applied or not grounding_decision.reply:
        return None

    safe_summary = {
        "provider_runtime": True,
        "rag_trace": summarize_rag_trace(runtime_context),
        "grounding_reason": grounding_decision.reason,
        "grounding_applied": True,
        "grounding_source": grounding_decision.source,
        "provider_bypassed": True,
        "provider_bypass_reason": "approved_direct_answer_pre_provider",
        "ai_decision": {
            "customer_reply": grounding_decision.reply,
            "intent": "general_support",
            "confidence": 1.0,
            "risk_level": "low",
            "next_action": "reply",
            "handoff_required": False,
            "handoff_reason": None,
            "tool_calls": [],
            "evidence_used": [{"source": "hybrid_rag_v2", "evidence_type": "locked_fact", "fact_evidence_present": True}],
            "safety_notes": ["provider bypassed by approved deterministic locked fact"],
        },
    }
    return FastAIProviderResult(
        ok=True,
        ai_generated=True,
        reply_source="codex_app_server:grounded_knowledge",
        raw_provider="knowledge_direct_answer",
        raw_payload_safe_summary=safe_summary,
        reply=grounding_decision.reply,
        intent="other",
        tracking_number=None,
        handoff_required=False,
        handoff_reason=None,
        recommended_agent_action=None,
        tool_intents=[],
        elapsed_ms=0,
    )


async def dispatch_webchat_fast_reply(*, request: FastAIProviderRequest) -> FastAIProviderResult:
    db = SessionLocal()
    try:
        runtime_context = request.metadata if isinstance(request.metadata, dict) and request.metadata.get("context_version") else None
        if runtime_context is None:
            try:
                runtime_context = build_webchat_runtime_context(
                    db,
                    tenant_key=request.tenant_key,
                    channel_key=request.channel_key,
                    body=request.body,
                    market_id=request.market_id,
                    language=request.language,
                )
            except Exception:
                logger.exception("webchat_runtime_context_build_failed")
                runtime_context = _fallback_runtime_context(request)

        pre_provider_result = _pre_provider_direct_answer_result(request=request, runtime_context=runtime_context)
        if pre_provider_result is not None:
            return pre_provider_result

        router = ProviderRuntimeRouter(db)
        res = await router.route(build_webchat_fast_provider_request(request, metadata=runtime_context))
        if not res.ok or not res.structured_output:
            return FastAIProviderResult.unavailable(
                provider="provider_runtime",
                error_code=res.error_code or "all_failed",
                elapsed_ms=res.elapsed_ms,
            )

        output = res.structured_output
        safe_summary = dict(res.raw_payload_safe_summary or {})
        safe_summary["provider_runtime"] = True
        safe_summary["rag_trace"] = summarize_rag_trace(runtime_context)
        knowledge_context = _knowledge_context(runtime_context)
        output, repair_trace = _repair_output_with_trusted_direct_answer(
            output=output,
            request=request,
            knowledge_context=knowledge_context,
        )
        if repair_trace:
            safe_summary.update(repair_trace)
        grounding_decision = None
        if _knowledge_reply_mode() == "deterministic_direct_answer":
            grounding_decision = select_approved_direct_answer_override(
                query=request.body,
                provider_output=output,
                knowledge_context=knowledge_context,
                tracking_fact_evidence_present=request.tracking_fact_evidence_present,
            )
            if not grounding_decision.applied and grounding_decision.reason != "trusted_tracking_output_conflict":
                grounding_decision = enforce_grounded_answer(
                    query=request.body,
                    provider_reply=output.get("customer_reply") or output.get("reply"),
                    hits=knowledge_context.get("hits", []) if isinstance(knowledge_context, dict) else [],
                    tracking_fact_evidence_present=request.tracking_fact_evidence_present,
                )
            safe_summary["grounding_reason"] = grounding_decision.reason
            safe_summary["grounding_applied"] = grounding_decision.applied
            if grounding_decision.source:
                safe_summary["grounding_source"] = grounding_decision.source
            if grounding_decision.applied and grounding_decision.reply:
                output = {
                    **output,
                    "customer_reply": grounding_decision.reply,
                    "intent": _grounded_intent(output.get("intent")),
                    "tracking_number": None,
                    "handoff_required": False,
                    "handoff_reason": None,
                    "recommended_agent_action": None,
                    "ticket_should_create": False,
                    "tool_calls": [],
                    "next_action": "reply",
                }
        else:
            if _ai_grounded_validation_failed(output, knowledge_context):
                return FastAIProviderResult.unavailable(
                    provider=res.provider,
                    error_code="locked_fact_grounding_conflict",
                    elapsed_ms=res.elapsed_ms,
                )
            safe_summary["provider_bypassed"] = False
            safe_summary.update(_ai_grounded_summary(output, knowledge_context))
        ai_decision = _ai_decision_summary_from_output(output)
        if ai_decision is not None:
            safe_summary["ai_decision"] = ai_decision
        reply = output.get("customer_reply") or output.get("reply")

        return FastAIProviderResult(
            ok=True,
            ai_generated=True,
            reply_source=res.provider if _knowledge_reply_mode() != "deterministic_direct_answer" else (
                f"{res.provider}:grounded_knowledge" if grounding_decision and grounding_decision.applied else res.provider
            ),
            raw_provider=res.provider,
            raw_payload_safe_summary=safe_summary,
            reply=reply,
            intent=output.get("intent"),
            tracking_number=output.get("tracking_number"),
            handoff_required=output.get("handoff_required", False),
            handoff_reason=output.get("handoff_reason"),
            recommended_agent_action=output.get("recommended_agent_action"),
            tool_intents=[],
            elapsed_ms=res.elapsed_ms,
        )
    except Exception:
        logger.exception("ProviderRuntimeRouter failed")
        return FastAIProviderResult.unavailable(
            provider="provider_runtime",
            error_code="router_exception",
            elapsed_ms=0,
        )
    finally:
        db.close()
