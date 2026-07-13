from __future__ import annotations

import pytest

from app.services.provider_runtime.schemas import ProviderRequest
from app.services.provider_runtime.traffic_selection import (
    ALLOWED_CANARY_PERCENTAGES,
    ProviderTrafficPath,
    normalize_persisted_boolean,
    safe_traffic_configuration,
    select_provider_traffic,
    stable_canary_bucket,
)


def _request(**overrides) -> ProviderRequest:
    values = {
        "request_id": "req-stable",
        "tenant_id": "tenant-1",
        "tenant_key": "tenant-key-1",
        "channel_key": "website",
        "session_id": "session-1",
        "scenario": "webchat_runtime_reply",
        "body": "hello",
        "output_contract": "nexus_webchat_runtime_reply_v1",
        "timeout_ms": 1000,
    }
    values.update(overrides)
    return ProviderRequest(**values)


@pytest.fixture(autouse=True)
def _clear_traffic_environment(monkeypatch):
    for name in (
        "PROVIDER_RUNTIME_TRAFFIC_MODE",
        "PROVIDER_RUNTIME_CANARY_PERCENT",
        "PROVIDER_RUNTIME_KILL_SWITCH",
    ):
        monkeypatch.delenv(name, raising=False)


def test_authorized_canary_stages_are_explicit():
    assert ALLOWED_CANARY_PERCENTAGES == frozenset({0, 1, 5, 25, 100})


@pytest.mark.parametrize("percent", [0, 1, 5, 25, 100])
def test_safe_configuration_accepts_only_authorized_stages(percent):
    snapshot = safe_traffic_configuration(
        default_canary_percent=percent,
        default_kill_switch=False,
        default_mode="canary",
    )

    assert snapshot["configuration_errors"] == []
    assert snapshot["canary_percent"] == percent
    assert snapshot["configured_mode"] == "canary"


@pytest.mark.parametrize("percent", [-1, 2, 101, True, "01", "5.0", "not-a-number"])
def test_safe_configuration_fails_closed_for_unsupported_canary_values(percent):
    snapshot = safe_traffic_configuration(
        default_canary_percent=percent,
        default_kill_switch=False,
        default_mode="canary",
    )

    assert "provider_runtime_canary_percent_invalid" in snapshot["configuration_errors"]
    assert snapshot["canary_percent"] is None
    assert snapshot["authoritative"] is False


@pytest.mark.parametrize(("raw", "expected"), [(False, False), (True, True), (0, False), (1, True)])
def test_persisted_sqlite_booleans_are_normalized(raw, expected):
    assert normalize_persisted_boolean(raw) is expected


@pytest.mark.parametrize("raw", [-1, 2, "0", "1", None])
def test_invalid_persisted_booleans_are_rejected(raw):
    with pytest.raises(ValueError, match="provider_runtime_kill_switch_invalid"):
        normalize_persisted_boolean(raw)


def test_bucket_is_stable_for_identical_server_owned_scope():
    first = stable_canary_bucket(_request(request_id="request-a"))
    second = stable_canary_bucket(_request(request_id="request-b"))

    assert first == second
    assert 0 <= first < 100


def test_control_mode_never_executes_candidate():
    selection = select_provider_traffic(
        _request(),
        canary_percent=100,
        kill_switch=False,
        configured_mode_value="control",
    )

    assert selection.path is ProviderTrafficPath.CONTROL
    assert selection.execute_candidate is False
    assert selection.authoritative is False


def test_shadow_mode_executes_but_is_never_authoritative():
    selection = select_provider_traffic(
        _request(),
        canary_percent=100,
        kill_switch=False,
        configured_mode_value="shadow",
    )

    assert selection.path is ProviderTrafficPath.SHADOW_ONLY
    assert selection.execute_candidate is True
    assert selection.authoritative is False


def test_zero_percent_canary_stays_on_control_path():
    selection = select_provider_traffic(
        _request(),
        canary_percent=0,
        kill_switch=False,
        configured_mode_value="canary",
    )

    assert selection.path is ProviderTrafficPath.CONTROL
    assert selection.execute_candidate is False
    assert selection.authoritative is False


def test_hundred_percent_canary_is_authoritative():
    selection = select_provider_traffic(
        _request(),
        canary_percent=100,
        kill_switch=False,
        configured_mode_value="canary",
    )

    assert selection.path is ProviderTrafficPath.CANARY_AUTHORITATIVE
    assert selection.execute_candidate is True
    assert selection.authoritative is True


def test_kill_switch_has_priority_over_invalid_lower_priority_configuration():
    selection = select_provider_traffic(
        _request(),
        canary_percent=2,
        kill_switch=True,
        configured_mode_value="unsupported",
    )

    assert selection.path is ProviderTrafficPath.KILL_SWITCH
    assert selection.execute_candidate is False
    assert selection.authoritative is False
    assert "provider_runtime_canary_percent_invalid" in selection.configuration_errors
    assert "provider_runtime_traffic_mode_invalid" in selection.configuration_errors
