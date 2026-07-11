from __future__ import annotations

from unittest.mock import Mock

from app.api.admin_provider_runtime import (
    WebchatRuntimeRoutingUpdate,
    provider_runtime_status,
    update_webchat_runtime_routing,
)


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
    assert rule["output_contract"] == "nexus_webchat_runtime_reply_v1"
    assert rule["canary_percent"] == 100
    assert rule["kill_switch"] is False
    assert rule["traffic_selection"]["schema_version"] == "nexus.provider_runtime.traffic_selection.v1"
    assert rule["traffic_selection"]["configured_mode"] == "canary"
    assert rule["traffic_selection"]["canary_percent"] == 100
    assert db.commit.called


def test_admin_provider_runtime_status_exposes_effective_traffic_authority(monkeypatch):
    monkeypatch.setattr("app.api.admin_provider_runtime.ensure_can_manage_runtime", lambda current_user, db: None)
    monkeypatch.setattr(
        "app.api.admin_provider_runtime.get_provider_runtime_status",
        lambda db: {"ok": True, "status": "ready"},
    )
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE", "shadow")
    monkeypatch.setenv("PROVIDER_RUNTIME_CANARY_PERCENT", "25")
    monkeypatch.setenv("PROVIDER_RUNTIME_KILL_SWITCH", "false")

    response = provider_runtime_status(db=Mock(), current_user=Mock())

    assert response["ok"] is True
    traffic = response["traffic_selection"]
    assert traffic["schema_version"] == "nexus.provider_runtime.traffic_selection.v1"
    assert traffic["configured_mode"] == "shadow"
    assert traffic["canary_percent"] == 25
    assert traffic["kill_switch"] is False
    assert traffic["bucket_contract"] == "sha256(tenant,channel,session,scenario)%100"
