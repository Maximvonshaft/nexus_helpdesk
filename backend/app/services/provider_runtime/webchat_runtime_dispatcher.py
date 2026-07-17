from __future__ import annotations

import logging
from typing import Any

from app.db import SessionLocal

from ..ai_runtime.schemas import RuntimeAIProviderRequest, RuntimeAIProviderResult
from ..ai_runtime_context import build_webchat_runtime_context
from ..customer_language import detect_customer_language
from ..knowledge_prompt_service import summarize_rag_trace
from .output_contracts import OutputContracts, WEBCHAT_RUNTIME_OUTPUT_CONTRACT
from .router import ProviderRuntimeRouter
from .schemas import ProviderRequest

logger = logging.getLogger(__name__)

WEBCHAT_RUNTIME_SCENARIO = "webchat_runtime_reply"


def _fallback_runtime_context(
    request: RuntimeAIProviderRequest,
) -> dict[str, Any]:
    return {
        "context_version": "nexus.webchat_runtime_context",
        "tenant_key": request.tenant_key,
        "metadata_filters": {
            "market_id": request.market_id,
            "channel": request.channel_key,
            "language": request.language,
            "audience_scope": "customer",
        },
        "persona_context": None,
        "knowledge_context": {
            "retrieval": "unavailable",
            "total_matches": 0,
            "locked_facts": [],
            "hits": [],
        },
        "safety_policy": {
            "knowledge_scope": "policy_sop_faq_only",
            "locked_facts_contract": "Use locked_facts as authoritative facts when present; never change numbers, country, service type, or policy boundaries.",
            "tracking_truth_boundary": "Parcel live status requires tracking_fact_evidence_present=true and trusted tracking_fact_summary.",
        },
    }


def build_webchat_runtime_provider_request(
    request: RuntimeAIProviderRequest,
    *,
    metadata: dict[str, Any] | None = None,
) -> ProviderRequest:
    safe_metadata = dict(metadata or {})
    if request.metadata:
        safe_metadata.update(request.metadata)
    language_decision = detect_customer_language(
        request.body,
        explicit=request.language,
    )
    safe_metadata["language"] = language_decision.language
    safe_metadata["customer_language"] = language_decision.language
    safe_metadata["customer_language_source"] = language_decision.source
    safe_metadata["reply_language_policy"] = "same_as_latest_customer_message"
    metadata_filters = dict(safe_metadata.get("metadata_filters") or {})
    metadata_filters["language"] = language_decision.language
    safe_metadata["metadata_filters"] = metadata_filters
    if request.tracking_fact_metadata:
        safe_metadata["tracking_fact_metadata"] = request.tracking_fact_metadata
    return ProviderRequest(
        request_id=request.request_id or "req_unknown",
        tenant_id=request.tenant_key,
        tenant_key=request.tenant_key,
        channel_key=request.channel_key,
        session_id=request.session_id,
        scenario=WEBCHAT_RUNTIME_SCENARIO,
        body=request.body,
        recent_context=request.recent_context,
        tracking_fact_summary=request.tracking_fact_summary,
        tracking_fact_evidence_present=request.tracking_fact_evidence_present,
        output_contract=WEBCHAT_RUNTIME_OUTPUT_CONTRACT,
        timeout_ms=10000,
        metadata=safe_metadata,
    )


def _knowledge_context(
    runtime_context: dict[str, Any] | None,
) -> dict[str, Any]:
    knowledge = (runtime_context or {}).get("knowledge_context") or {}
    return knowledge if isinstance(knowledge, dict) else {}


def _ai_grounding_summary(
    output: dict[str, Any],
    knowledge_context: dict[str, Any],
    *,
    request_body: str | None = None,
    tracking_fact_evidence_present: bool = False,
) -> dict[str, Any]:
    reply = output.get("customer_reply") or output.get("reply")
    if OutputContracts._trusted_tracking_reply_can_bypass_locked_facts(
        evidence_present=tracking_fact_evidence_present,
        request_body=request_body,
        parsed=output,
    ):
        return {
            "grounding_validation": "skipped",
            "grounding_applied": False,
            "locked_fact_ids": [],
            "grounding_reason": "trusted_tracking_fact_reply",
        }
    validation_context = knowledge_context
    try:
        from .adapters.private_ai_runtime import (
            _customer_intent_hint,
            _customer_visible_knowledge_context,
        )

        intent_hint = _customer_intent_hint(request_body)
        compact_context = _customer_visible_knowledge_context(
            knowledge_context,
            direct_answer_only=intent_hint == "service_or_policy",
            derive_locked_facts=intent_hint == "service_or_policy",
        )
        if compact_context.get("locked_facts"):
            validation_context = compact_context
    except Exception:
        validation_context = knowledge_context
    validation = OutputContracts.locked_fact_validation(
        reply,
        validation_context,
    )
    summary = {
        "grounding_validation": validation["status"],
        "grounding_applied": validation["status"] == "pass",
        "locked_fact_ids": validation.get("locked_fact_ids") or [],
    }
    if validation.get("source"):
        summary["grounding_source"] = validation["source"]
    if validation["status"] == "pass":
        summary["grounding_reason"] = "locked_fact_ai_grounded"
    return summary


def _ai_decision_summary_from_output(
    output: dict[str, Any],
) -> dict[str, Any] | None:
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
        "risk_level": output.get("risk_level")
        or ("medium" if handoff_required else "low"),
        "next_action": output.get("next_action")
        or ("request_handoff" if handoff_required else "reply"),
        "handoff_required": handoff_required,
        "handoff_reason": output.get("handoff_reason"),
        "tool_calls": (
            output.get("tool_calls")
            if isinstance(output.get("tool_calls"), list)
            else []
        ),
        "evidence_used": (
            output.get("evidence_used")
            if isinstance(output.get("evidence_used"), list)
            else []
        ),
        "safety_notes": (
            output.get("safety_notes")
            if isinstance(output.get("safety_notes"), list)
            else []
        ),
    }


async def dispatch_webchat_runtime_reply(
    *,
    request: RuntimeAIProviderRequest,
) -> RuntimeAIProviderResult:
    db = SessionLocal()
    try:
        runtime_context = (
            request.metadata
            if isinstance(request.metadata, dict)
            and request.metadata.get("context_version")
            else None
        )
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

        router = ProviderRuntimeRouter(db)
        provider_request = build_webchat_runtime_provider_request(
            request,
            metadata=runtime_context,
        )
        result = await router.route(provider_request)
        if not result.ok or not result.structured_output:
            safe_summary = dict(result.raw_payload_safe_summary or {})
            safe_summary["provider_runtime"] = True
            safe_summary["provider_bypassed"] = False
            return RuntimeAIProviderResult.unavailable(
                provider="provider_runtime",
                error_code=result.error_code or "all_failed",
                elapsed_ms=result.elapsed_ms,
                safe_summary=safe_summary,
            )

        output = result.structured_output
        safe_summary = dict(result.raw_payload_safe_summary or {})
        safe_summary["provider_runtime"] = True
        safe_summary["rag_trace"] = summarize_rag_trace(runtime_context)
        safe_summary["provider_bypassed"] = False
        grounding_summary = _ai_grounding_summary(
            output,
            _knowledge_context(runtime_context),
            request_body=str(request.body or ""),
            tracking_fact_evidence_present=(
                request.tracking_fact_evidence_present
            ),
        )
        safe_summary.update(grounding_summary)
        if (
            safe_summary.get("output_contract_repair_reason")
            == "locked_fact_grounding_conflict"
            and grounding_summary.get("grounding_validation") != "pass"
        ):
            safe_summary["error_code"] = "locked_fact_grounding_conflict"
            safe_summary["grounding_violation"] = (
                "provider_runtime_locked_fact_conflict"
            )
            return RuntimeAIProviderResult.unavailable(
                provider="provider_runtime",
                error_code="locked_fact_grounding_conflict",
                elapsed_ms=result.elapsed_ms,
                safe_summary=safe_summary,
            )
        if grounding_summary.get("grounding_validation") == "fail":
            safe_summary["error_code"] = "locked_fact_grounding_conflict"
            return RuntimeAIProviderResult.unavailable(
                provider="provider_runtime",
                error_code="locked_fact_grounding_conflict",
                elapsed_ms=result.elapsed_ms,
                safe_summary=safe_summary,
            )
        ai_decision = _ai_decision_summary_from_output(output)
        if ai_decision is not None:
            safe_summary["ai_decision"] = ai_decision

        reply = output.get("customer_reply") or output.get("reply")
        return RuntimeAIProviderResult(
            ok=True,
            ai_generated=True,
            reply_source=result.provider,
            raw_provider=result.provider,
            raw_payload_safe_summary=safe_summary,
            reply=reply,
            intent=output.get("intent"),
            tracking_number=output.get("tracking_number"),
            handoff_required=output.get("handoff_required", False),
            handoff_reason=output.get("handoff_reason"),
            recommended_agent_action=output.get(
                "recommended_agent_action"
            ),
            tool_intents=[],
            elapsed_ms=result.elapsed_ms,
        )
    except Exception:
        logger.exception("ProviderRuntimeRouter failed")
        return RuntimeAIProviderResult.unavailable(
            provider="provider_runtime",
            error_code="router_exception",
            elapsed_ms=0,
        )
    finally:
        db.close()
