from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from app.api.admin_provider_runtime import (
    WebchatRuntimeRoutingUpdate,
    provider_runtime_status,
    update_webchat_runtime_routing,
)


def _db_with_routing_rows(rows):
    db = Mock()
    result = Mock()
    result.mappings.return_value.all.return_value = list(rows)
    db.execute.return_value = result
    return db


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
    assert rule["traffic_selection"]["configuration_errors"] == []
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

    response = provider_runtime_status(db=_db_with_routing_rows([]), current_user=Mock())

    assert response["ok"] is True
    traffic = response["traffic_selection"]
    assert traffic["schema_version"] == "nexus.provider_runtime.traffic_selection.v1"
    assert traffic["configured_mode"] == "shadow"
    assert traffic["canary_percent"] == 25
    assert traffic["kill_switch"] is False
    assert traffic["configuration_errors"] == []
    assert traffic["bucket_contract"] == "sha256(tenant,channel,session,scenario)%100"
    assert traffic["scope"] == "global_defaults_and_environment_overrides"
    assert traffic["webchat_runtime_rules"] == []


def test_admin_provider_runtime_status_reports_effective_database_rules(monkeypatch):
    monkeypatch.setattr("app.api.admin_provider_runtime.ensure_can_manage_runtime", lambda current_user, db: None)
    monkeypatch.setattr(
        "app.api.admin_provider_runtime.get_provider_runtime_status",
        lambda db: {"ok": True, "status": "ready"},
    )
    db = _db_with_routing_rows(
        [
            {
                "tenant_id": "tenant-a",
                "channel_key": "website",
                "primary_provider": "private_ai_runtime",
                "canary_percent": 5,
                "kill_switch": False,
                "enabled": True,
                "updated_at": datetime(2026, 7, 11, tzinfo=timezone.utc),
            }
        ]
    )

    response = provider_runtime_status(db=db, current_user=Mock())

    rules = response["traffic_selection"]["webchat_runtime_rules"]
    assert len(rules) == 1
    rule = rules[0]
    assert rule["tenant_id"] == "tenant-a"
    assert rule["database_canary_percent"] == 5
    assert rule["effective_traffic_selection"]["canary_percent"] == 5
    assert rule["effective_traffic_selection"]["configured_mode"] == "canary"
    assert rule["effective_traffic_selection"]["configuration_errors"] == []


@pytest.mark.parametrize(
    ("environment", "value", "expected_error"),
    [
        ("PROVIDER_RUNTIME_TRAFFIC_MODE", "invalid", "provider_runtime_traffic_mode_invalid"),
        ("PROVIDER_RUNTIME_CANARY_PERCENT", "invalid", "provider_runtime_canary_percent_invalid"),
        ("PROVIDER_RUNTIME_KILL_SWITCH", "invalid", "provider_runtime_kill_switch_invalid"),
    ],
)
def test_admin_provider_runtime_status_fails_closed_on_invalid_traffic_configuration(
    monkeypatch,
    environment,
    value,
    expected_error,
):
    monkeypatch.setattr("app.api.admin_provider_runtime.ensure_can_manage_runtime", lambda current_user, db: None)
    monkeypatch.setattr(
        "app.api.admin_provider_runtime.get_provider_runtime_status",
        lambda db: {"ok": True, "status": "ready", "warnings": []},
    )
    monkeypatch.setenv(environment, value)

    response = provider_runtime_status(db=_db_with_routing_rows([]), current_user=Mock())

    assert response["ok"] is False
    assert response["status"] == "misconfigured"
    assert expected_error in response["traffic_selection"]["configuration_errors"]
    assert any(expected_error in warning for warning in response["warnings"])
