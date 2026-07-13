from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from app.api.admin_provider_runtime import (
    WebchatRuntimeRoutingUpdate,
    provider_runtime_status,
    update_webchat_runtime_routing,
)

_BUCKET_CONTRACT = "sha256(tenant_id,tenant_key,channel_key,session_id,scenario)%100"


def _db_with_routing_rows(rows):
    db = Mock()
    result = Mock()
    result.mappings.return_value.all.return_value = list(rows)
    db.execute.return_value = result
    return db


def _routing_row(*, canary_percent=5, kill_switch=False):
    return {
        "tenant_id": "tenant-a",
        "channel_key": "website",
        "primary_provider": "private_ai_runtime",
        "canary_percent": canary_percent,
        "kill_switch": kill_switch,
        "enabled": True,
        "updated_at": datetime(2026, 7, 11, tzinfo=timezone.utc),
    }


def _stub_admin_dependencies(monkeypatch):
    monkeypatch.setattr("app.api.admin_provider_runtime.ensure_can_manage_runtime", lambda current_user, db: None)
    monkeypatch.setattr(
        "app.api.admin_provider_runtime.get_provider_runtime_status",
        lambda db: {"ok": True, "status": "ready", "warnings": []},
    )


def test_admin_provider_runtime_routing_api_inserts_rule_without_granting_default_authority(monkeypatch):
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
    traffic = rule["traffic_selection"]
    assert traffic["schema_version"] == "nexus.provider_runtime.traffic_selection.v1"
    assert traffic["configured_mode"] == "control"
    assert traffic["default_canary_percent"] == 100
    assert traffic["canary_percent"] == 100
    assert traffic["configuration_errors"] == []
    assert db.commit.called


@pytest.mark.parametrize(
    ("payload", "expected_error"),
    [
        (WebchatRuntimeRoutingUpdate(primary_provider="unexpected"), "primary_provider_not_allowed"),
        (WebchatRuntimeRoutingUpdate(fallback_providers=["unexpected"]), "fallback_provider_not_allowed"),
        (WebchatRuntimeRoutingUpdate(canary_percent=2), "provider_runtime_canary_percent_invalid"),
    ],
)
def test_admin_routing_rejection_exposes_only_fixed_error_codes(monkeypatch, payload, expected_error):
    monkeypatch.setattr("app.api.admin_provider_runtime.ensure_can_manage_runtime", lambda current_user, db: None)
    db = Mock()

    with pytest.raises(HTTPException) as caught:
        update_webchat_runtime_routing(payload, db=db, current_user=Mock())

    assert caught.value.status_code == 400
    assert caught.value.detail == {"error_code": expected_error}
    assert "traceback" not in str(caught.value.detail).lower()
    db.execute.assert_not_called()
    db.commit.assert_not_called()


def test_admin_provider_runtime_status_exposes_effective_traffic_authority(monkeypatch):
    _stub_admin_dependencies(monkeypatch)
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
    assert traffic["bucket_contract"] == _BUCKET_CONTRACT
    assert traffic["scope"] == "global_defaults_and_environment_overrides"
    assert traffic["webchat_runtime_rules"] == {
        "status": "ready",
        "reason_code": None,
        "items": [],
        "truncated": False,
    }


def test_admin_provider_runtime_status_reports_database_rule_under_safe_control_default(monkeypatch):
    _stub_admin_dependencies(monkeypatch)
    db = _db_with_routing_rows([_routing_row()])

    response = provider_runtime_status(db=db, current_user=Mock())

    assert response["ok"] is True
    assert response["traffic_selection"]["configured_mode"] == "control"
    assert response["traffic_selection"]["canary_percent"] == 0
    routing_state = response["traffic_selection"]["webchat_runtime_rules"]
    assert routing_state["status"] == "ready"
    assert routing_state["truncated"] is False
    assert len(routing_state["items"]) == 1
    rule = routing_state["items"][0]
    assert rule["tenant_id"] == "tenant-a"
    assert rule["database_canary_percent"] == 5
    assert rule["database_kill_switch"] is False
    assert rule["database_configuration_errors"] == []
    assert rule["effective_traffic_selection"]["default_canary_percent"] == 5
    assert rule["effective_traffic_selection"]["canary_percent"] == 5
    assert rule["effective_traffic_selection"]["configured_mode"] == "control"
    assert rule["effective_traffic_selection"]["configuration_errors"] == []


@pytest.mark.parametrize(
    ("canary_percent", "kill_switch", "expected_error"),
    [
        (101, False, "provider_runtime_canary_percent_invalid"),
        (2, False, "provider_runtime_canary_percent_invalid"),
        (True, False, "provider_runtime_canary_percent_invalid"),
        (5, "false", "provider_runtime_kill_switch_invalid"),
    ],
)
def test_admin_status_marks_invalid_persisted_rule_as_misconfigured(
    monkeypatch,
    canary_percent,
    kill_switch,
    expected_error,
):
    _stub_admin_dependencies(monkeypatch)
    db = _db_with_routing_rows(
        [_routing_row(canary_percent=canary_percent, kill_switch=kill_switch)]
    )

    response = provider_runtime_status(db=db, current_user=Mock())

    assert response["ok"] is False
    assert response["status"] == "misconfigured"
    routing_state = response["traffic_selection"]["webchat_runtime_rules"]
    assert routing_state["status"] == "misconfigured"
    assert routing_state["reason_code"] == "provider_runtime_routing_rule_invalid"
    item = routing_state["items"][0]
    assert item["database_configuration_errors"] == [expected_error]
    assert expected_error in item["effective_traffic_selection"]["configuration_errors"]
    assert "provider_runtime routing rules are misconfigured" in response["warnings"]


def test_admin_provider_runtime_status_fails_closed_when_rule_query_fails(monkeypatch):
    _stub_admin_dependencies(monkeypatch)
    db = Mock()
    db.execute.side_effect = RuntimeError("database unavailable")

    response = provider_runtime_status(db=db, current_user=Mock())

    assert response["ok"] is False
    assert response["status"] == "unavailable"
    routing_state = response["traffic_selection"]["webchat_runtime_rules"]
    assert routing_state["status"] == "unavailable"
    assert routing_state["reason_code"] == "provider_runtime_routing_rules_unavailable"
    assert "provider_runtime routing rules are unavailable" in response["warnings"]


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
    _stub_admin_dependencies(monkeypatch)
    monkeypatch.setenv(environment, value)

    response = provider_runtime_status(db=_db_with_routing_rows([]), current_user=Mock())

    assert response["ok"] is False
    assert response["status"] == "misconfigured"
    assert expected_error in response["traffic_selection"]["configuration_errors"]
    assert any(expected_error in warning for warning in response["warnings"])
