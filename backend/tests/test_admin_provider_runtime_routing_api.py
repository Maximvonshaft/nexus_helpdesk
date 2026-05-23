from __future__ import annotations

from unittest.mock import Mock

from app.api.admin_provider_runtime import WebchatFastRoutingUpdate, update_webchat_fast_reply_routing


def test_admin_provider_runtime_routing_api_inserts_safe_default(monkeypatch):
    monkeypatch.setattr("app.api.admin_provider_runtime.ensure_can_manage_runtime", lambda current_user, db: None)
    db = Mock()
    select_result = Mock()
    select_result.mappings.return_value.first.return_value = None
    db.execute.return_value = select_result

    response = update_webchat_fast_reply_routing(
        WebchatFastRoutingUpdate(canary_percent=1),
        db=db,
        current_user=Mock(),
    )

    assert response["ok"] is True
    rule = response["routing_rule"]
    assert rule["primary_provider"] == "codex_app_server"
    assert rule["fallback_providers"] == ["openclaw_responses", "rule_engine"]
    assert rule["canary_percent"] == 1
    assert rule["kill_switch"] is False
    assert db.commit.called
