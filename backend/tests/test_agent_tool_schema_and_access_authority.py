from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.agent_runtime.access_policy import resolve_webchat_agent_access
from app.services.webchat_ai_decision_runtime.policy_gate import validate_ai_decision
from app.services.webchat_ai_decision_runtime.schemas import AIDecision, AIDecisionToolCall
from app.services.webchat_ai_decision_runtime.tool_registry import get_tool_contract


def _unvalidated_tool_decision(tool_name: str, arguments: dict) -> AIDecision:
    return AIDecision.model_construct(
        customer_reply=None,
        intent="test_tool_contract",
        next_action="call_tool",
        handoff_required=False,
        tool_calls=[
            AIDecisionToolCall.model_construct(
                tool_name=tool_name,
                arguments=arguments,
                idempotency_key=None,
                reason=None,
                requires_confirmation=None,
            )
        ],
        evidence_used=[],
        safety_notes=[],
    )


def test_agent_turn_boundary_blocks_missing_required_tool_argument() -> None:
    with pytest.raises(ValidationError, match="registered input schema"):
        AIDecision.model_validate(
            {
                "customer_reply": None,
                "intent": "knowledge_lookup",
                "next_action": "call_tool",
                "tool_calls": [
                    {"tool_name": "knowledge.search", "arguments": {}}
                ],
            }
        )


def test_agent_turn_boundary_blocks_unknown_properties_without_echoing_values() -> None:
    secret = "do-not-persist-this-value"
    with pytest.raises(ValidationError) as caught:
        AIDecision.model_validate(
            {
                "customer_reply": None,
                "intent": "knowledge_lookup",
                "next_action": "call_tool",
                "tool_calls": [
                    {
                        "tool_name": "knowledge.search",
                        "arguments": {
                            "query": "approved policy",
                            "raw_payload": secret,
                        },
                    }
                ],
            }
        )

    message = str(caught.value)
    assert "additionalProperties" in message
    assert secret not in message


def test_policy_gate_defense_blocks_unvalidated_malformed_arguments() -> None:
    result = validate_ai_decision(
        _unvalidated_tool_decision("knowledge.search", {}),
        granted_permissions={"knowledge:read"},
    )

    assert result.ok is False
    assert result.checked_tools == ("knowledge.search",)
    violation = result.violations[0]
    assert violation.code == "tool_input_schema_invalid"
    assert violation.tool_name == "knowledge.search"
    assert "required" in violation.message


def test_policy_gate_accepts_arguments_matching_registered_schema() -> None:
    decision = AIDecision.model_validate(
        {
            "customer_reply": None,
            "intent": "knowledge_lookup",
            "next_action": "call_tool",
            "tool_calls": [
                {
                    "tool_name": "knowledge.search",
                    "arguments": {"query": "approved policy", "limit": 3},
                }
            ],
        }
    )
    result = validate_ai_decision(
        decision,
        granted_permissions={"knowledge:read"},
    )

    assert result.ok is True
    assert result.violations == ()


def test_public_webchat_defaults_are_least_privilege(monkeypatch) -> None:
    monkeypatch.delenv("WEBCHAT_AGENT_ALLOWED_TOOLS", raising=False)
    monkeypatch.delenv("WEBCHAT_AGENT_GRANTED_PERMISSIONS", raising=False)

    policy = resolve_webchat_agent_access()

    assert "ticket.create" not in policy.allowed_tools
    assert "timeline.event.create" not in policy.allowed_tools
    assert "ticket:create" not in policy.granted_permissions
    assert "timeline:event:create" not in policy.granted_permissions
    assert all(
        not get_tool_contract(tool_name).confirmation_required
        for tool_name in policy.allowed_tools
    )


def test_public_webchat_cannot_enable_confirmation_required_tool_by_env(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "WEBCHAT_AGENT_ALLOWED_TOOLS",
        "knowledge.search,ticket.create",
    )
    monkeypatch.setenv(
        "WEBCHAT_AGENT_GRANTED_PERMISSIONS",
        "knowledge:read,ticket:create",
    )

    policy = resolve_webchat_agent_access()

    assert policy.allowed_tools == ("knowledge.search",)
    assert policy.granted_permissions == frozenset(
        {"knowledge:read", "ticket:create"}
    )
