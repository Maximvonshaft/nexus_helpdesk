from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.services.knowledge_prompt_service import summarize_rag_trace
from app.services.tracking_fact_schema import hash_tracking_number
from app.services.webchat_fast_output_parser import FastReplyParseError, assert_customer_visible_reply_is_safe

from .audit import log_ai_decision_audit
from .policy_gate import PolicyGateResult, validate_ai_decision
from .schemas import AI_DECISION_SCHEMA_VERSION, AIDecision, AIDecisionEvidence, AIDecisionToolCall, normalize_intent, normalize_next_action, normalize_risk_level


_HANDOFF_INTENTS = {"handoff_request", "refusal_request", "address_change", "complaint"}


def _clip(value: Any, limit: int) -> str | None:
    cleaned = " ".join(str(value or "").strip().split())
    return cleaned[:limit] if cleaned else None


def _tracking_fact_evidence(metadata: dict[str, Any] | None) -> AIDecisionEvidence | None:
    if not isinstance(metadata, dict):
        return None
    fact_present = bool(metadata.get("fact_evidence_present") and metadata.get("pii_redacted"))
    source = "speedaf_trusted_tracking_fact" if fact_present else "speedaf_tracking_fact_unavailable"
    tracking_hash = metadata.get("tracking_number_hash")
    return AIDecisionEvidence(
        source=source,
        evidence_type="trusted_tracking_fact",
        evidence_id=str(metadata.get("tool_status") or metadata.get("tracking_fact_failure_reason") or source)[:240],
        fact_evidence_present=fact_present,
        tracking_number_hash=str(tracking_hash) if tracking_hash else None,
        raw_tracking_number_exposed=False,
    )


def _rag_evidence(runtime_context: dict[str, Any] | None) -> AIDecisionEvidence | None:
    if not isinstance(runtime_context, dict):
        return None
    trace = summarize_rag_trace(runtime_context)
    if not isinstance(trace, dict):
        return None
    return AIDecisionEvidence(
        source="hybrid_rag_v2",
        evidence_type="knowledge_context",
        evidence_id=str(trace.get("top_item_key") or trace.get("retrieval") or "hybrid_rag_v2")[:240],
        fact_evidence_present=bool(trace.get("total_matches") or trace.get("candidate_count") or trace.get("evidence_pack")),
        raw_tracking_number_exposed=False,
    )


def _default_tool_calls(*, intent: str, handoff_required: bool, tracking_number: str | None, tracking_fact_metadata: dict[str, Any] | None, handoff_reason: str | None) -> list[AIDecisionToolCall]:
    calls: list[AIDecisionToolCall] = []
    if intent == "tracking" and tracking_number:
        calls.append(
            AIDecisionToolCall(
                tool_name="speedaf.order.query",
                arguments={"tracking_number_hash": hash_tracking_number(tracking_number)},
                reason="trusted_tracking_fact_required_for_live_status",
                requires_confirmation=False,
            )
        )
    if handoff_required or intent in _HANDOFF_INTENTS:
        reason = _clip(handoff_reason, 240) or f"{intent}_requires_human_review"
        calls.append(
            AIDecisionToolCall(
                tool_name="handoff.request.create",
                arguments={"reason": reason, "intent": intent},
                reason="ai_requested_human_review_through_controlled_tool",
                requires_confirmation=False,
            )
        )
    return calls


def _decision_payload_from_provider(provider_result: Any, *, tracking_fact_metadata: dict[str, Any] | None = None, tracking_number: str | None = None, runtime_context: dict[str, Any] | None = None) -> dict[str, Any]:
    safe_summary = getattr(provider_result, "raw_payload_safe_summary", None) or {}
    raw_decision = safe_summary.get("ai_decision") if isinstance(safe_summary, dict) else None
    if isinstance(raw_decision, dict):
        payload = dict(raw_decision)
    else:
        payload = {}
    reply = payload.get("customer_reply") or getattr(provider_result, "reply", None) or payload.get("reply")
    intent = normalize_intent(payload.get("intent") or getattr(provider_result, "intent", None))
    handoff_required = bool(payload.get("handoff_required", getattr(provider_result, "handoff_required", False)))
    provider_tracking = _clip(payload.get("tracking_number") or getattr(provider_result, "tracking_number", None) or tracking_number, 120)
    evidence_used = list(payload.get("evidence_used") or []) if isinstance(payload.get("evidence_used") or [], list) else []
    tracking_evidence = _tracking_fact_evidence(tracking_fact_metadata)
    if tracking_evidence is not None:
        evidence_used.append(tracking_evidence.model_dump(exclude_none=True))
    rag_evidence = _rag_evidence(runtime_context)
    if rag_evidence is not None:
        evidence_used.append(rag_evidence.model_dump(exclude_none=True))
    tool_calls = list(payload.get("tool_calls") or []) if isinstance(payload.get("tool_calls") or [], list) else []
    if not tool_calls:
        tool_calls = [call.model_dump(exclude_none=True) for call in _default_tool_calls(intent=intent, handoff_required=handoff_required, tracking_number=provider_tracking, tracking_fact_metadata=tracking_fact_metadata, handoff_reason=payload.get("handoff_reason") or getattr(provider_result, "handoff_reason", None))]
    return {
        "customer_reply": reply,
        "intent": intent,
        "confidence": float(payload.get("confidence", 0.7 if getattr(provider_result, "ok", False) else 0.0) or 0.0),
        "risk_level": normalize_risk_level(payload.get("risk_level") or ("medium" if handoff_required else "low")),
        "next_action": normalize_next_action(payload.get("next_action"), handoff_required=handoff_required, has_tool_calls=bool(tool_calls), intent=intent),
        "handoff_required": handoff_required,
        "handoff_reason": _clip(payload.get("handoff_reason") or getattr(provider_result, "handoff_reason", None), 240),
        "tool_calls": tool_calls,
        "evidence_used": evidence_used,
        "safety_notes": payload.get("safety_notes") or [],
    }


def decision_from_provider_result(
    provider_result: Any,
    *,
    tracking_fact_metadata: dict[str, Any] | None = None,
    tracking_number: str | None = None,
    runtime_context: dict[str, Any] | None = None,
) -> AIDecision:
    try:
        decision = AIDecision.model_validate(
            _decision_payload_from_provider(
                provider_result,
                tracking_fact_metadata=tracking_fact_metadata,
                tracking_number=tracking_number,
                runtime_context=runtime_context,
            )
        )
        assert_customer_visible_reply_is_safe(decision.customer_reply)
        return decision
    except (ValidationError, FastReplyParseError, ValueError) as exc:
        raise FastReplyParseError(f"AI decision output is invalid: {exc}") from exc


def build_ai_decision_trace(
    *,
    decision: AIDecision,
    policy_result: PolicyGateResult,
    reply_source: str | None = None,
    mode: str = "gated",
    runtime_context: dict[str, Any] | None = None,
    tool_execution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    trace: dict[str, Any] = {
        "schema_version": AI_DECISION_SCHEMA_VERSION,
        "mode": mode,
        "reply_source": reply_source,
        "decision": decision.safe_public_summary(),
        "policy_gate": policy_result.safe_summary(),
        "tool_execution": tool_execution or {"ok": True, "records": []},
        "raw_tracking_number_exposed": False,
    }
    if runtime_context:
        trace["runtime_context_trace"] = summarize_rag_trace(runtime_context)
    return trace


def validate_and_trace_decision(
    *,
    decision: AIDecision,
    tracking_fact_metadata: dict[str, Any] | None = None,
    tracking_number: str | None = None,
    reply_source: str | None = None,
    runtime_context: dict[str, Any] | None = None,
    mode: str = "gated",
    request_id: str | None = None,
    tenant_key: str | None = None,
    channel_key: str | None = None,
    session_id: str | None = None,
) -> tuple[PolicyGateResult, dict[str, Any]]:
    policy = validate_ai_decision(decision, tracking_fact_metadata=tracking_fact_metadata, tracking_number=tracking_number)
    trace = build_ai_decision_trace(decision=decision, policy_result=policy, reply_source=reply_source, mode=mode, runtime_context=runtime_context)
    log_ai_decision_audit(
        event="webchat_ai_decision_validated",
        request_id=request_id,
        tenant_key=tenant_key,
        channel_key=channel_key,
        session_id=session_id,
        payload=trace,
    )
    return policy, trace
