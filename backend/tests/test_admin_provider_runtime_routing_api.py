from __future__ import annotations

from unittest.mock import Mock

from app.api.admin_provider_runtime import WebchatRuntimeRoutingUpdate, update_webchat_runtime_routing


def test_admin_provider_runtime_routing_api_inserts_safe_default(monkeypatch):
    monkeypatch.setattr("app.api.admin_provider_runtime.ensure_can_manage_runtime", lambda current_user, db: None)
    db = Mock()
    select_result = Mock()
    select_result.mappings.return_value.first.return_value = None
    db.execute.return_value = select_result

    response = update_webchat_runtime_routing(
        WebchatRuntimeRoutingUpdate(canary_percent=100),
        db=db,
        current_user=Mock(),
    )

    assert response["ok"] is True
    rule = response["routing_rule"]
    assert rule["scenario"] == "webchat_runtime_reply"
    assert rule["primary_provider"] == "private_ai_runtime"
    assert rule["fallback_providers"] == []
    assert rule["output_contract"] == "nexus.webchat_runtime_reply"
    assert rule["canary_percent"] == 100
    assert rule["kill_switch"] is False
    assert db.commit.called
