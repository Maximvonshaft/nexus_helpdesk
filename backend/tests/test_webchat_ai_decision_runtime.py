from __future__ import annotations

import pytest

from app.services.webchat_ai_decision_runtime.policy_gate import validate_ai_decision
from app.services.webchat_ai_decision_runtime.schemas import AIDecision, AIDecisionToolCall
from app.services.webchat_ai_decision_runtime.tool_registry import (
    canonical_tool_name,
    get_tool_contract,
    registered_tool_names,
    safe_registry_summary,
)


def test_tool_registry_contains_complete_canonical_contracts():
    required = {
        "knowledge.search",
        "speedaf.order.query",
        "speedaf.express.track.query",
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
        assert contract.description
        assert contract.input_schema["type"] == "object"
        assert contract.classification in {"read", "write", "system"}
        assert contract.required_permissions
        assert contract.risk_level in {"low", "medium", "high"}
        assert contract.allowed_auto_execution_mode in {
            "auto",
            "policy_gated",
            "confirmation_required",
            "disabled",
        }
    assert {item["name"] for item in safe_registry_summary()} == set(registered_tool_names())


def test_tool_names_are_exact_and_aliases_are_not_supported():
    assert canonical_tool_name("  knowledge.search  ") == "knowledge.search"
    assert get_tool_contract("knowledge.search") is not None
    for retired_alias in (
        "support_knowledge_retrieve",
        "speedaf_lookup",
        "speedaf_query_waybills",
        "speedaf_create_work_order",
        "speedaf_cancel_order",
        "speedaf_update_address",
    ):
        assert get_tool_contract(retired_alias) is None
        with pytest.raises(ValueError, match="registered canonical Tool"):
            AIDecisionToolCall(tool_name=retired_alias, arguments={})


def test_unknown_tool_is_rejected_by_agent_turn_schema():
    with pytest.raises(ValueError, match="registered canonical Tool"):
        AIDecisionToolCall(tool_name="database.write.anything", arguments={})


def test_tool_permission_is_enforced_generically():
    decision = AIDecision(
        customer_reply=None,
        intent="knowledge_lookup",
        next_action="call_tool",
        tool_calls=[
            AIDecisionToolCall(
                tool_name="knowledge.search",
                arguments={"query": "approved policy"},
            )
        ],
    )

    denied = validate_ai_decision(decision, granted_permissions={"ticket:create"})
    allowed = validate_ai_decision(decision, granted_permissions={"knowledge:read"})

    assert denied.ok is False
    assert {item.code for item in denied.violations} == {"tool_permission_denied"}
    assert allowed.ok is True


def test_confirmation_and_high_risk_write_policy_are_tool_contract_driven():
    decision = AIDecision(
        customer_reply=None,
        intent="address_update",
        confidence=0.9,
        risk_level="high",
        next_action="call_tool",
        tool_calls=[
            AIDecisionToolCall(
                tool_name="speedaf.order.updateAddress.request",
                arguments={
                    "tracking_number": "CH020000006856",
                    "address": "Confirmed address",
                },
                requires_confirmation=False,
            )
        ],
    )

    blocked = validate_ai_decision(decision)
    codes = {item.code for item in blocked.violations}
    assert blocked.ok is False
    assert "write_tool_confirmation_required" in codes
    assert "high_risk_write_tool_blocked" in codes

    confirmed = decision.model_copy(
        update={
            "tool_calls": [decision.tool_calls[0].model_copy(update={"requires_confirmation": True})]
        }
    )
    allowed = validate_ai_decision(
        confirmed,
        allow_high_risk_write_execution=True,
        allowed_high_risk_write_tools={"speedaf.order.updateAddress.request"},
    )
    assert allowed.ok is True


def test_customer_visible_policy_blocks_secrets_not_business_words():
    ordinary = AIDecision(
        customer_reply="Your parcel has been delivered.",
        intent="shipment_tracking",
        next_action="reply",
    )
    assert validate_ai_decision(ordinary).ok is True

    credential = ("Bear" + "er ") + ("a" * 30)
    unsafe = AIDecision(
        customer_reply=credential,
        intent="support",
        next_action="reply",
    )
    result = validate_ai_decision(unsafe)
    assert result.ok is False
    assert {item.code for item in result.violations} == {"unsafe_customer_reply"}
