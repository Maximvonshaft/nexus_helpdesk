from __future__ import annotations

from app.services.tracking_fact_schema import hash_tracking_number
from app.services.webchat_ai_decision_runtime.policy_gate import validate_ai_decision
from app.services.webchat_ai_decision_runtime.schemas import AIDecision, AIDecisionEvidence, AIDecisionToolCall
from app.services.webchat_ai_decision_runtime.tool_registry import get_tool_contract, registered_tool_names, safe_registry_summary


def test_tool_registry_contains_required_contract_fields():
    required = {
        "knowledge.search",
        "speedaf.order.query",
        "speedaf.order.waybillCode.query",
        "handoff.request.create",
        "ticket.create",
        "conversation.suspend_ai",
        "conversation.resume_ai",
        "speedaf.workOrder.create",
        "speedaf.order.cancel.request",
        "speedaf.order.updateAddress.request",
        "speedaf.voice.callback",
        "timeline.event.create",
    }
    assert required.issubset(set(registered_tool_names()))
    for name in required:
        contract = get_tool_contract(name)
        assert contract is not None
        assert contract.name == name
        assert contract.classification in {"read", "write", "system"}
        assert contract.required_permissions
        assert contract.idempotency_key_strategy
        assert contract.risk_level in {"low", "medium", "high"}
        assert contract.redaction_requirements
        assert contract.allowed_auto_execution_mode in {"auto", "policy_gated", "confirmation_required", "disabled"}
    assert {item["name"] for item in safe_registry_summary()} == set(registered_tool_names())


def test_unknown_tool_is_blocked():
    decision = AIDecision(
        customer_reply="I can help with that.",
        intent="general_support",
        confidence=0.8,
        risk_level="low",
        next_action="call_tool",
        handoff_required=False,
        tool_calls=[AIDecisionToolCall(tool_name="database.write.anything", arguments={})],
        evidence_used=[],
        safety_notes=[],
    )

    result = validate_ai_decision(decision)

    assert result.ok is False
    assert result.violations[0].code == "unknown_tool_blocked"


def test_write_tool_requires_confirmation_and_is_blocked_in_phase_one():
    decision = AIDecision(
        customer_reply="I can request cancellation after a human verifies it.",
        intent="general_support",
        confidence=0.8,
        risk_level="high",
        next_action="call_tool",
        handoff_required=False,
        tool_calls=[AIDecisionToolCall(tool_name="speedaf.order.cancel.request", arguments={"reason_code": "CC01"})],
        evidence_used=[],
        safety_notes=[],
    )

    result = validate_ai_decision(decision)

    assert result.ok is False
    codes = {violation.code for violation in result.violations}
    assert "write_tool_confirmation_required" in codes
    assert "high_risk_write_tool_blocked" in codes


def test_handoff_requires_registered_handoff_tool():
    decision = AIDecision(
        customer_reply="A human teammate will review this request.",
        intent="handoff_request",
        confidence=0.8,
        risk_level="medium",
        next_action="request_handoff",
        handoff_required=True,
        handoff_reason="customer_requested_human_review",
        tool_calls=[],
        evidence_used=[],
        safety_notes=[],
    )

    result = validate_ai_decision(decision)

    assert result.ok is False
    assert any(violation.code == "handoff_tool_missing" for violation in result.violations)


def test_tracking_status_claim_requires_trusted_fact():
    decision = AIDecision(
        customer_reply="Your parcel ending 006856 is currently delivered.",
        intent="tracking",
        confidence=0.8,
        risk_level="medium",
        next_action="call_tool",
        handoff_required=False,
        tool_calls=[AIDecisionToolCall(tool_name="speedaf.order.query", arguments={"tracking_number_hash": hash_tracking_number("CH020000006856")})],
        evidence_used=[],
        safety_notes=[],
    )

    result = validate_ai_decision(decision, tracking_fact_metadata={"fact_evidence_present": False, "pii_redacted": True}, tracking_number="CH020000006856")

    assert result.ok is False
    assert any(violation.code == "tracking_status_without_trusted_fact" for violation in result.violations)


def test_tracking_status_claim_passes_with_trusted_fact_and_redacted_evidence():
    tracking_number = "CH020000006856"
    decision = AIDecision(
        customer_reply="Your parcel ending 006856 is currently in transit.",
        intent="tracking",
        confidence=0.9,
        risk_level="medium",
        next_action="call_tool",
        handoff_required=False,
        tool_calls=[AIDecisionToolCall(tool_name="speedaf.order.query", arguments={"tracking_number_hash": hash_tracking_number(tracking_number)})],
        evidence_used=[
            AIDecisionEvidence(
                source="speedaf_trusted_tracking_fact",
                evidence_type="trusted_tracking_fact",
                fact_evidence_present=True,
                tracking_number_hash=hash_tracking_number(tracking_number),
                raw_tracking_number_exposed=False,
            )
        ],
        safety_notes=[],
    )

    result = validate_ai_decision(
        decision,
        tracking_fact_metadata={"fact_evidence_present": True, "pii_redacted": True, "tracking_number_hash": hash_tracking_number(tracking_number)},
        tracking_number=tracking_number,
    )

    assert result.ok is True


def test_raw_waybill_caller_and_secret_are_blocked_from_reply():
    decision = AIDecision(
        customer_reply="Use CH020000006856 and Bearer abcdefghijklmnopqrstuvwxyz123456 to check +41000009999.",
        intent="general_support",
        confidence=0.8,
        risk_level="high",
        next_action="reply",
        handoff_required=False,
        tool_calls=[],
        evidence_used=[],
        safety_notes=[],
    )

    result = validate_ai_decision(decision, tracking_number="CH020000006856")

    assert result.ok is False
    codes = {violation.code for violation in result.violations}
    assert "raw_tracking_exposed" in codes
    assert "raw_caller_or_secret_exposed" in codes or "unsafe_customer_reply" in codes
