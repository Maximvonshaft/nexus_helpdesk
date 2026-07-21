from __future__ import annotations

from unittest.mock import Mock

from app.api.admin_provider_runtime import (
    AgentTurnRoutingUpdate,
    update_agent_turn_routing,
)


def test_admin_agent_routing_api_aligns_parent_and_specialist(monkeypatch):
    monkeypatch.setattr(
        "app.api.admin_provider_runtime.ensure_can_manage_runtime",
        lambda current_user, db: None,
    )
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
    parent = response["routing_rule"]
    assert parent["scenario"] == "agent_turn"
    assert parent["primary_provider"] == "private_ai_runtime"
    assert parent["fallback_providers"] == []
    assert parent["output_contract"] == "nexus.agent_turn.v1"
    assert parent["canary_percent"] == 100
    assert parent["kill_switch"] is False

    rules = {item["scenario"]: item for item in response["routing_rules"]}
    assert set(rules) == {"agent_turn", "agent_specialist"}
    specialist = rules["agent_specialist"]
    assert specialist["output_contract"] == "nexus.agent_specialist.v1"
    for field in (
        "tenant_id",
        "channel_key",
        "primary_provider",
        "fallback_providers",
        "timeout_ms",
        "canary_percent",
        "kill_switch",
        "enabled",
    ):
        assert specialist[field] == parent[field]
    assert db.commit.call_count == 1
