from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

from app.services.knowledge_grounding_service import is_explicit_handoff_or_business_action, select_trusted_direct_answer_evidence
from app.services.knowledge_prompt_service import summarize_rag_trace
from app.services.tracking_fact_schema import hash_tracking_number
from app.services.webchat_fast_output_parser import FastReplyParseError, assert_customer_visible_reply_is_safe

from .audit import log_ai_decision_audit
from .policy_gate import PolicyGateResult, validate_ai_decision
from .schemas import AI_DECISION_SCHEMA_VERSION, AIDecision, AIDecisionEvidence, AIDecisionToolCall, normalize_intent, normalize_next_action, normalize_risk_level


_HANDOFF_INTENTS = {"handoff_request", "refusal_request", "address_change", "complaint"}
_TRACKING_EVIDENCE_SOURCES = {"speedaf_trusted_tracking_fact", "speedaf.order.query", "speedaf_tracking_fact_unavailable"}


def _clip(value: Any, limit: int) -> str | None:
    cleaned = " ".join(str(value or "").strip().split())
    return cleaned[:limit] if cleaned else None


def _trusted_tracking_fact_present(metadata: dict[str, Any] | None) -> bool:
    return bool(isinstance(metadata, dict) and metadata.get("fact_evidence_present") and metadata.get("pii_redacted"))


def _trusted_tracking_hash(*, metadata: dict[str, Any] | None, tracking_number: str | None) -> str | None:
    metadata_hash = metadata.get("tracking_number_hash") if isinstance(metadata, dict) else None
    if isinstance(metadata_hash, str) and metadata_hash.startswith("sha256:"):
        return metadata_hash
    if tracking_number:
        return hash_tracking_number(tracking_number)
    return None


def _tracking_suffix(tracking_number: str | None) -> str | None:
    cleaned = "".join(ch for ch in str(tracking_number or "").strip().upper() if ch.isalnum())
    return cleaned[-6:] if cleaned else None


def _sanitize_reply_for_trusted_tracking(reply: Any, *, tracking_number: str | None, tracking_fact_metadata: dict[str, Any] | None) -> Any:
    if not isinstance(reply, str) or not _trusted_tracking_fact_present(tracking_fact_metadata):
        return reply
    raw = str(tracking_number or "").strip()
    if not raw:
        return reply
    suffix = _tracking_suffix(raw)
    if not suffix:
        return reply
    replacement = f"tracking number ending {suffix}"
    return re.sub(re.escape(raw), replacement, reply, flags=re.IGNORECASE)


def _provider_evidence_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value[:20] if isinstance(item, dict)]


def _normalized_provider_evidence(value: Any, *, tracking_fact_metadata: dict[str, Any] | None) -> list[dict[str, Any]]:
    evidence_used = _provider_evidence_items(value)
    if not _trusted_tracking_fact_present(tracking_fact_metadata):
        return evidence_used
    return [
        item
        for item in evidence_used
        if str(item.get("source") or "") not in _TRACKING_EVIDENCE_SOURCES
    ]


def _normalized_provider_tool_calls(value: Any, *, intent: str, tracking_number: str | None, tracking_fact_metadata: dict[str, Any] | None) -> list[dict[str, Any]]:
    tool_calls = list(value or []) if isinstance(value or [], list) else []
    trusted_tracking = _trusted_tracking_fact_present(tracking_fact_metadata)
    tracking_hash = _trusted_tracking_hash(metadata=tracking_fact_metadata, tracking_number=tracking_number)
    normalized: list[dict[str, Any]] = []
    has_speedaf_query = False
    for item in tool_calls[:12]:
        if not isinstance(item, dict):
            continue
        data = dict(item)
        tool_name = data.get("tool_name") or data.get("name") or data.get("tool")
        if tool_name == "speedaf.order.query":
            has_speedaf_query = True
            args = data.get("arguments") if isinstance(data.get("arguments"), dict) else {}
            args = dict(args)
            args.pop("tracking_number", None)
            args.pop("waybill", None)
            if tracking_hash:
                args["tracking_number_hash"] = tracking_hash
            data["arguments"] = args
            data["requires_confirmation"] = False
        normalized.append(data)
    if trusted_tracking and intent == "tracking" and tracking_number and not has_speedaf_query:
        normalized.extend(
            call.model_dump(exclude_none=True)
            for call in _default_tool_calls(
                intent=intent,
                handoff_required=False,
                tracking_number=tracking_number,
                tracking_fact_metadata=tracking_fact_metadata,
                handoff_reason=None,
            )
        )
    return normalized


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



def _decision_knowledge_context(runtime_context: dict[str, Any] | None) -> dict[str, Any]:
    knowledge = runtime_context.get("knowledge_context") if isinstance(runtime_context, dict) else None
    return knowledge if isinstance(knowledge, dict) else {}


def _decision_requests_handoff_or_fallback(payload: dict[str, Any], provider_result: Any) -> bool:
    intent = normalize_intent(payload.get("intent") or getattr(provider_result, "intent", None))
    next_action = str(payload.get("next_action") or "").strip().lower()
    reply = str(payload.get("customer_reply") or getattr(provider_result, "reply", None) or payload.get("reply") or "").strip().lower()
    calls = payload.get("tool_calls") if isinstance(payload.get("tool_calls"), list) else []
    return bool(
        payload.get("handoff_required", getattr(provider_result, "handoff_required", False)) is True
        or intent in _HANDOFF_INTENTS
        or next_action == "request_handoff"
        or any(isinstance(call, dict) and (call.get("tool_name") or call.get("name") or call.get("tool")) == "handoff.request.create" for call in calls)
        or "human teammate" in reply
        or "support team will check" in reply
        or "support specialist will check" in reply
        or "temporarily unavailable" in reply
        or "人工" in reply
        or "暂时不可用" in reply
    )


def _apply_trusted_kb_direct_answer_decision_repair(
    *,
    payload: dict[str, Any],
    provider_result: Any,
    tracking_fact_metadata: dict[str, Any] | None,
    runtime_context: dict[str, Any] | None,
    request_body: str | None,
) -> dict[str, Any]:
    if _trusted_tracking_fact_present(tracking_fact_metadata):
        return payload
    if is_explicit_handoff_or_business_action(request_body):
        return payload
    if not _decision_requests_handoff_or_fallback(payload, provider_result):
        return payload

    selected = select_trusted_direct_answer_evidence(
        _decision_knowledge_context(runtime_context),
        query=request_body,
        tracking_fact_evidence_present=False,
    )
    if not selected.applied or not selected.reply:
        return payload

    source = selected.source if isinstance(selected.source, dict) else {}
    evidence_id = str(source.get("item_key") or source.get("title") or "trusted_direct_answer")[:240]
    return {
        **payload,
        "customer_reply": selected.reply,
        "intent": "general_support",
        "confidence": 1.0,
        "risk_level": "low",
        "next_action": "reply",
        "handoff_required": False,
        "handoff_reason": None,
        "tool_calls": [],
        "tracking_number": None,
        "evidence_used": [
            {
                "source": "hybrid_rag_v2",
                "evidence_type": "knowledge_context",
                "evidence_id": evidence_id,
                "fact_evidence_present": True,
                "raw_tracking_number_exposed": False,
                "repair_applied": True,
                "repair_reason": "trusted_kb_direct_answer_decision_repair",
            }
        ],
        "safety_notes": ["trusted KB direct_answer repaired provider handoff/fallback decision"],
    }


def _decision_payload_from_provider(provider_result: Any, *, tracking_fact_metadata: dict[str, Any] | None = None, tracking_number: str | None = None, runtime_context: dict[str, Any] | None = None, request_body: str | None = None) -> dict[str, Any]:
    safe_summary = getattr(provider_result, "raw_payload_safe_summary", None) or {}
    raw_decision = safe_summary.get("ai_decision") if isinstance(safe_summary, dict) else None
    if isinstance(raw_decision, dict):
        payload = dict(raw_decision)
    else:
        payload = {}
    payload = _apply_trusted_kb_direct_answer_decision_repair(
        payload=payload,
        provider_result=provider_result,
        tracking_fact_metadata=tracking_fact_metadata,
        runtime_context=runtime_context,
        request_body=request_body,
    )
    intent = normalize_intent(payload.get("intent") or getattr(provider_result, "intent", None))
    handoff_required = bool(payload.get("handoff_required", getattr(provider_result, "handoff_required", False)))
    provider_tracking = _clip(payload.get("tracking_number") or getattr(provider_result, "tracking_number", None) or tracking_number, 120)
    reply = _sanitize_reply_for_trusted_tracking(
        payload.get("customer_reply") or getattr(provider_result, "reply", None) or payload.get("reply"),
        tracking_number=provider_tracking,
        tracking_fact_metadata=tracking_fact_metadata,
    )
    evidence_used = _normalized_provider_evidence(payload.get("evidence_used"), tracking_fact_metadata=tracking_fact_metadata)
    tracking_evidence = _tracking_fact_evidence(tracking_fact_metadata)
    if tracking_evidence is not None:
        evidence_used.append(tracking_evidence.model_dump(exclude_none=True))
    rag_evidence = _rag_evidence(runtime_context)
    if rag_evidence is not None:
        evidence_used.append(rag_evidence.model_dump(exclude_none=True))
    tool_calls = _normalized_provider_tool_calls(
        payload.get("tool_calls"),
        intent=intent,
        tracking_number=provider_tracking,
        tracking_fact_metadata=tracking_fact_metadata,
    )
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
    request_body: str | None = None,
) -> AIDecision:
    try:
        decision = AIDecision.model_validate(
            _decision_payload_from_provider(
                provider_result,
                tracking_fact_metadata=tracking_fact_metadata,
                tracking_number=tracking_number,
                runtime_context=runtime_context,
                request_body=request_body,
            )
        )
        try:
            assert_customer_visible_reply_is_safe(decision.customer_reply)
        except FastReplyParseError as exc:
            if not (_trusted_tracking_fact_present(tracking_fact_metadata) and decision.intent == "tracking" and "unsafe business promise" in str(exc)):
                raise
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
    if any(
        isinstance(getattr(evidence, "model_extra", None), dict)
        and evidence.model_extra.get("repair_applied")
        for evidence in decision.evidence_used
    ):
        trace["repair_applied"] = True
        trace["repair_reason"] = "trusted_kb_direct_answer_decision_repair"
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
