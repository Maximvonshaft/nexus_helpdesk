import pytest
from pydantic import ValidationError

from app.services.webcall_ai.schemas import (
    WebCallAIActionDecision,
    WebCallAIAllowedAction,
    WebCallAIForbiddenAction,
    WebCallAITurnDecision,
    reject_forbidden_action,
)


@pytest.mark.parametrize("action", [item.value for item in WebCallAIAllowedAction])
def test_allowed_actions_validate(action):
    decision = WebCallAITurnDecision(action=action, confidence=80)

    assert decision.action == action


@pytest.mark.parametrize("action", [item.value for item in WebCallAIForbiddenAction])
def test_forbidden_actions_are_rejected(action):
    with pytest.raises((ValidationError, ValueError), match=action):
        WebCallAITurnDecision(action=action)

    with pytest.raises(ValueError, match=action):
        reject_forbidden_action(action)


@pytest.mark.parametrize("nexus_decision", ["allowed", "blocked", "handoff", "failed"])
def test_nexus_decision_accepts_only_contract_values(nexus_decision):
    decision = WebCallAIActionDecision(
        model_action="lookup_tracking",
        nexus_decision=nexus_decision,
    )

    assert decision.nexus_decision == nexus_decision


def test_nexus_decision_rejects_unknown_value():
    with pytest.raises(ValidationError, match="nexus_decision"):
        WebCallAIActionDecision(model_action="lookup_tracking", nexus_decision="executed")


def test_delivery_followup_is_request_concept_not_executable_write():
    decision = WebCallAIActionDecision(
        model_action="request_delivery_followup",
        nexus_decision="handoff",
        decision_reason="Foundation PR records request intent only.",
    )

    assert decision.model_action == "request_delivery_followup"
    assert decision.nexus_decision == "handoff"

    with pytest.raises(ValidationError, match="speedaf.work_order.create"):
        WebCallAIActionDecision(
            model_action="request_delivery_followup",
            nexus_decision="allowed",
            speedaf_tool_name="speedaf.work_order.create",
        )
