from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from .audit import log_ai_decision_audit
from .policy_gate import PolicyGateResult, validate_ai_decision
from .schemas import AI_DECISION_SCHEMA_VERSION, AIDecision


def decision_from_provider_result(provider_result: Any, **_legacy: Any) -> AIDecision:
    payload = getattr(provider_result, "structured_output", None)
    if not isinstance(payload, dict):
        safe_summary = getattr(provider_result, "raw_payload_safe_summary", None)
        if isinstance(safe_summary, dict) and isinstance(safe_summary.get("ai_decision"), dict):
            payload = safe_summary["ai_decision"]
    if not isinstance(payload, dict):
        payload = {
            "customer_reply": getattr(provider_result, "reply", None),
            "intent": getattr(provider_result, "intent", None) or "general_support",
            "next_action": "request_handoff" if getattr(provider_result, "handoff_required", False) else "reply",
            "handoff_required": bool(getattr(provider_result, "handoff_required", False)),
            "handoff_reason": getattr(provider_result, "handoff_reason", None),
            "tool_calls": getattr(provider_result, "tool_calls", None) or [],
        }
    try:
        return AIDecision.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"AI decision output is invalid: {exc}") from exc


def build_ai_decision_trace(
    *,
    decision: AIDecision,
    policy_result: PolicyGateResult,
    reply_source: str | None = None,
    mode: str = "gated",
    tool_execution: dict[str, Any] | None = None,
    **_: Any,
) -> dict[str, Any]:
    return {
        "schema_version": AI_DECISION_SCHEMA_VERSION,
        "mode": mode,
        "reply_source": reply_source,
        "decision": decision.safe_public_summary(),
        "policy_gate": policy_result.safe_summary(),
        "tool_execution": tool_execution or {"ok": True, "records": []},
    }


def validate_and_trace_decision(
    *,
    decision: AIDecision,
    reply_source: str | None = None,
    mode: str = "gated",
    request_id: str | None = None,
    tenant_key: str | None = None,
    channel_key: str | None = None,
    session_id: str | None = None,
    **kwargs: Any,
) -> tuple[PolicyGateResult, dict[str, Any]]:
    policy = validate_ai_decision(decision, **kwargs)
    trace = build_ai_decision_trace(
        decision=decision,
        policy_result=policy,
        reply_source=reply_source,
        mode=mode,
    )
    log_ai_decision_audit(
        event="agent_decision_validated",
        request_id=request_id,
        tenant_key=tenant_key,
        channel_key=channel_key,
        session_id=session_id,
        payload=trace,
    )
    return policy, trace
