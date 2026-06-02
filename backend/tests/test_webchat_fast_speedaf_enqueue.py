from __future__ import annotations

import os

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_fast_speedaf_enqueue_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

from app.api import webchat_fast  # noqa: E402
from app.services.webchat_ai_decision_runtime.policy_gate import validate_ai_decision  # noqa: E402
from app.services.webchat_ai_decision_runtime.schemas import AIDecision, AIDecisionToolCall  # noqa: E402
from app.services.webchat_ai_decision_runtime.tool_registry import get_tool_contract  # noqa: E402


def test_caller_id_is_extracted_from_visitor_phone():
    visitor = webchat_fast.WebchatFastVisitor(phone="  +41 79 000 0000  ")

    assert webchat_fast._caller_id(visitor) == "+41 79 000 0000"
    assert webchat_fast._caller_id(None) is None


def test_speedaf_work_order_tool_is_registered_as_high_risk_confirmation_required():
    contract = get_tool_contract("speedaf.workOrder.create")

    assert contract is not None
    assert contract.classification == "write"
    assert contract.risk_level == "high"
    assert contract.confirmation_required is True
    assert contract.controlled_action_required is True
    assert contract.allowed_auto_execution_mode == "confirmation_required"
    assert "speedaf:work_order:create" in contract.required_permissions
    assert "hash_waybill" in contract.redaction_requirements


def test_speedaf_write_tool_without_confirmation_is_blocked_by_policy_gate():
    decision = AIDecision(
        customer_reply="A human teammate will verify the delivery follow-up before creating a work order.",
        intent="complaint",
        confidence=0.8,
        risk_level="high",
        next_action="call_tool",
        handoff_required=False,
        tool_calls=[
            AIDecisionToolCall(
                tool_name="speedaf.workOrder.create",
                arguments={
                    "waybill_hash": "sha256:test",
                    "caller_id_hash": "sha256:test-caller",
                    "work_order_type": "WT0103-05",
                },
                requires_confirmation=False,
            )
        ],
        evidence_used=[],
        safety_notes=[],
    )

    result = validate_ai_decision(decision)

    assert result.ok is False
    codes = {violation.code for violation in result.violations}
    assert "write_tool_confirmation_required" in codes
    assert "high_risk_write_tool_blocked" in codes


def test_speedaf_write_tool_with_confirmation_is_still_not_auto_executed_in_phase_one():
    decision = AIDecision(
        customer_reply="A human teammate will verify the delivery follow-up before creating a work order.",
        intent="complaint",
        confidence=0.8,
        risk_level="high",
        next_action="call_tool",
        handoff_required=False,
        tool_calls=[
            AIDecisionToolCall(
                tool_name="speedaf.workOrder.create",
                arguments={
                    "waybill_hash": "sha256:test",
                    "caller_id_hash": "sha256:test-caller",
                    "work_order_type": "WT0103-05",
                },
                requires_confirmation=True,
            )
        ],
        evidence_used=[],
        safety_notes=[],
    )

    result = validate_ai_decision(decision)

    assert result.ok is False
    assert any(violation.code == "high_risk_write_tool_blocked" for violation in result.violations)
