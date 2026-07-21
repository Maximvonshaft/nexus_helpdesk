from __future__ import annotations

from unittest.mock import Mock

from app.api.admin_provider_runtime import AgentTurnRoutingUpdate, update_agent_turn_routing


def test_admin_agent_turn_routing_api_inserts_safe_default(monkeypatch):
    monkeypatch.setattr("app.api.admin_provider_runtime.ensure_can_manage_runtime", lambda current_user, db: None)
    db = Mock()
    select_result = Mock()
    select_result.mappings.return_value.first.return_value = None
    db.execute.return_value = select_result

    response = update_agent_turn_routing(
        AgentTurnRoutingUpdate(canary_percent=100),
        db=db,
        current_user=Mock(),
    )

    assert response["ok"] is True
    rule = response["routing_rule"]
    assert rule["scenario"] == "agent_turn"
    assert rule["primary_provider"] == "private_ai_runtime"
    assert rule["fallback_providers"] == []
    assert rule["output_contract"] == "nexus.agent_turn.v1"
    assert rule["canary_percent"] == 100
    assert rule["kill_switch"] is False
    assert db.commit.called
