from __future__ import annotations

from unittest.mock import Mock

import pytest
from pydantic import ValidationError

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
    assert rule["traffic_selection"]["configuration_errors"] == []
    assert db.commit.called


@pytest.mark.parametrize("value", [2, True, "5", 5.0])
def test_admin_provider_runtime_rejects_non_strict_canary_stage_input(value):
    with pytest.raises(ValidationError) as exc_info:
        WebchatRuntimeRoutingUpdate(canary_percent=value)

    assert "provider_runtime_canary_percent_invalid" in str(exc_info.value)


def test_admin_status_normalizes_sqlite_boolean_rules(monkeypatch):
    monkeypatch.setattr("app.api.admin_provider_runtime.ensure_can_manage_runtime", lambda current_user, db: None)
    monkeypatch.setattr(
        "app.api.admin_provider_runtime.get_provider_runtime_status",
        lambda db: {"ok": True, "status": "ready", "warnings": []},
    )
    for name in (
        "PROVIDER_RUNTIME_TRAFFIC_MODE",
        "PROVIDER_RUNTIME_CANARY_PERCENT",
        "PROVIDER_RUNTIME_KILL_SWITCH",
    ):
        monkeypatch.delenv(name, raising=False)

    db = Mock()
    result = Mock()
    result.mappings.return_value.all.return_value = [
        {
            "tenant_id": "default",
            "channel_key": "website",
            "primary_provider": "private_ai_runtime",
            "canary_percent": 5,
            "kill_switch": 0,
            "enabled": 1,
            "updated_at": None,
        }
    ]
    db.execute.return_value = result

    response = provider_runtime_status(db=db, current_user=Mock())

    rules = response["traffic_selection"]["webchat_runtime_rules"]
    assert rules["status"] == "ready"
    assert rules["items"][0]["traffic_selection"]["kill_switch"] is False
    assert rules["items"][0]["traffic_selection"]["canary_percent"] == 5


def test_admin_status_marks_invalid_persisted_stage_misconfigured(monkeypatch):
    monkeypatch.setattr("app.api.admin_provider_runtime.ensure_can_manage_runtime", lambda current_user, db: None)
    monkeypatch.setattr(
        "app.api.admin_provider_runtime.get_provider_runtime_status",
        lambda db: {"ok": True, "status": "ready", "warnings": []},
    )
    for name in (
        "PROVIDER_RUNTIME_TRAFFIC_MODE",
        "PROVIDER_RUNTIME_CANARY_PERCENT",
        "PROVIDER_RUNTIME_KILL_SWITCH",
    ):
        monkeypatch.delenv(name, raising=False)

    db = Mock()
    result = Mock()
    result.mappings.return_value.all.return_value = [
        {
            "tenant_id": "default",
            "channel_key": "website",
            "primary_provider": "private_ai_runtime",
            "canary_percent": 2,
            "kill_switch": 0,
            "enabled": 1,
            "updated_at": None,
        }
    ]
    db.execute.return_value = result

    response = provider_runtime_status(db=db, current_user=Mock())

    assert response["ok"] is False
    assert response["status"] == "misconfigured"
    assert response["traffic_selection"]["webchat_runtime_rules"]["status"] == "misconfigured"
