from __future__ import annotations

from app.services.tool_governance import classify_tool_type, evaluate_tool_call_policy


def test_speedaf_read_tools_are_read_only():
    assert classify_tool_type("speedaf.order.query") == "read_only"
    assert classify_tool_type("speedaf.order.waybillCode.query") == "read_only"


def test_speedaf_write_tools_are_write_actions():
    assert classify_tool_type("speedaf.workOrder.create") == "write_action"
    assert classify_tool_type("speedaf.order.cancel.request") == "write_action"
    assert classify_tool_type("speedaf.order.updateAddress.request") == "write_action"


def test_speedaf_voice_callback_is_system_tool():
    assert classify_tool_type("speedaf.voice.callback") == "write_action"


def test_speedaf_write_tools_require_capability_in_enforce_mode(monkeypatch):
    monkeypatch.setenv("TOOL_GOVERNANCE_ENFORCEMENT_MODE", "enforce")
    decision = evaluate_tool_call_policy(tool_name="speedaf.workOrder.create")
    assert decision.allowed is False
    assert decision.required_capability == "tool:speedaf.workOrder.create:write"

    allowed = evaluate_tool_call_policy(
        tool_name="speedaf.workOrder.create",
        actor_capabilities={"tool:speedaf.workOrder.create:write"},
    )
    assert allowed.allowed is True
