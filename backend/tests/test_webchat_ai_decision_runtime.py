from __future__ import annotations

from types import SimpleNamespace

from app.services.tracking_fact_schema import hash_tracking_number
from app.services.webchat_ai_decision_runtime.policy_gate import validate_ai_decision
from app.services.webchat_ai_decision_runtime.schemas import AIDecision, AIDecisionEvidence, AIDecisionToolCall
from app.services.webchat_ai_decision_runtime.service import decision_from_provider_result
from app.services.webchat_ai_decision_runtime.tool_registry import canonical_tool_name, get_tool_contract, registered_tool_names, safe_registry_summary


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


def test_legacy_support_agent_tool_names_normalize_to_nexus_contracts():
    aliases = {
        "support_knowledge_retrieve": "knowledge.search",
        "speedaf_lookup": "speedaf.order.query",
        "speedaf_query_waybills": "speedaf.order.waybillCode.query",
        "speedaf_create_work_order": "speedaf.workOrder.create",
        "speedaf_cancel_order": "speedaf.order.cancel.request",
        "speedaf_update_address": "speedaf.order.updateAddress.request",
        "speedaf.work_order.create": "speedaf.workOrder.create",
        "speedaf.order.cancel": "speedaf.order.cancel.request",
        "speedaf.order.update_address": "speedaf.order.updateAddress.request",
    }
    for alias, canonical in aliases.items():
        assert canonical_tool_name(alias) == canonical
        contract = get_tool_contract(alias)
        assert contract is not None
        assert contract.name == canonical


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


def test_legacy_write_tool_alias_keeps_confirmation_and_phase_one_block():
    decision = AIDecision(
        customer_reply="I can request address update after a human verifies it.",
        intent="address_change",
        confidence=0.8,
        risk_level="high",
        next_action="call_tool",
        handoff_required=False,
        tool_calls=[AIDecisionToolCall(tool_name="speedaf_update_address", arguments={"tracking_number_hash": "sha256:test"})],
        evidence_used=[],
        safety_notes=[],
    )

    assert decision.tool_calls[0].tool_name == "speedaf.order.updateAddress.request"
    result = validate_ai_decision(decision)

    assert result.ok is False
    codes = {violation.code for violation in result.violations}
    assert "write_tool_confirmation_required" in codes
    assert "high_risk_write_tool_blocked" in codes


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


def test_trusted_tracking_decision_sanitizes_raw_waybill_and_tool_args():
    tracking_number = "CH020000006856"
    tracking_hash = hash_tracking_number(tracking_number)
    provider_result = SimpleNamespace(
        ok=True,
        reply="Your package CH020000006856 is in transit.",
        intent="tracking",
        handoff_required=False,
        handoff_reason=None,
        tracking_number=None,
        raw_payload_safe_summary={
            "ai_decision": {
                "customer_reply": "Your package CH020000006856 is in transit.",
                "intent": "tracking",
                "confidence": 0.9,
                "risk_level": "low",
                "next_action": "call_tool",
                "handoff_required": False,
                "handoff_reason": None,
                "tool_calls": [
                    {"tool_name": "speedaf.order.query", "arguments": {"tracking_number": tracking_number}},
                ],
                "evidence_used": [
                    {
                        "source": "speedaf_trusted_tracking_fact",
                        "evidence_type": "trusted_tracking_fact",
                        "fact_evidence_present": True,
                        "tracking_number_hash": "not-a-valid-hash",
                        "raw_tracking_number_exposed": True,
                    }
                ],
                "safety_notes": [],
            }
        },
    )

    decision = decision_from_provider_result(
        provider_result,
        tracking_fact_metadata={"fact_evidence_present": True, "pii_redacted": True, "tracking_number_hash": tracking_hash},
        tracking_number=tracking_number,
    )

    assert tracking_number not in decision.customer_reply
    assert "006856" in decision.customer_reply
    assert decision.tool_calls[0].tool_name == "speedaf.order.query"
    assert decision.tool_calls[0].arguments == {"tracking_number_hash": tracking_hash}
    assert any(
        item.source == "speedaf_trusted_tracking_fact"
        and item.fact_evidence_present is True
        and item.tracking_number_hash == tracking_hash
        and item.raw_tracking_number_exposed is False
        for item in decision.evidence_used
    )
    result = validate_ai_decision(
        decision,
        tracking_fact_metadata={"fact_evidence_present": True, "pii_redacted": True, "tracking_number_hash": tracking_hash},
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





def test_runtime_trace_tracking_number_is_redacted_from_public_trace():
    from app.api.webchat_fast import _fallback_runtime_trace, _redact_tracking_number_from_public_trace

    tracking_number = "CH120000011425"
    raw_trace = {
        "retrieval": "hybrid_rag_v2",
        "query_analysis": {
            "normalized_query": "track ch120000011425",
            "numeric_terms": ["ch120000011425"],
            "high_value_terms": ["CH120000011425", "tracking"],
        },
        "candidate_count": 0,
        "total_matches": 0,
    }

    redacted = _redact_tracking_number_from_public_trace(raw_trace, tracking_number)
    rendered = str(redacted).lower()

    assert tracking_number.lower() not in rendered
    assert "tracking_number_ending_011425" in rendered

    fallback_trace = _fallback_runtime_trace(raw_trace, tracking_number=tracking_number)
    assert tracking_number.lower() not in str(fallback_trace).lower()
