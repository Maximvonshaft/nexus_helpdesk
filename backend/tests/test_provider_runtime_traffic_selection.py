from __future__ import annotations

import pytest

from app.services.provider_runtime.schemas import ProviderRequest
from app.services.provider_runtime.traffic_selection import (
    ProviderTrafficPath,
    configured_runtime_enabled,
    select_provider_traffic,
    stable_canary_bucket,
    validate_canary_percent,
)


def _request(*, session_id: str = "session-1", request_id: str = "request-1") -> ProviderRequest:
    return ProviderRequest(
        request_id=request_id,
        tenant_id="tenant-1",
        tenant_key="tenant-key",
        channel_key="webchat",
        session_id=session_id,
        scenario="webchat_runtime_reply",
        body="hello",
        output_contract="nexus.webchat_runtime_reply",
        timeout_ms=1000,
    )


def test_stable_bucket_is_identity_based_not_request_id_based():
    first = stable_canary_bucket(_request(request_id="request-a"))
    second = stable_canary_bucket(_request(request_id="request-b"))
    assert first == second


def test_stable_bucket_changes_with_session_identity():
    buckets = {
        stable_canary_bucket(_request(session_id=f"session-{index}"))
        for index in range(20)
    }
    assert len(buckets) > 1


@pytest.mark.parametrize("value", [0, 1, 5, 25, 100, "25"])
def test_supported_canary_percentages(value):
    assert validate_canary_percent(value) == int(value)


@pytest.mark.parametrize("value", [-1, 2, 10, 101, True, "25.0", None])
def test_unsupported_canary_percentages_fail_closed(value):
    with pytest.raises(ValueError, match="provider_runtime_canary_percent_invalid"):
        validate_canary_percent(value)


@pytest.mark.parametrize("value", [True, 1, "true", "yes", "on"])
def test_explicit_runtime_enable_values(value):
    assert configured_runtime_enabled(value) is True


@pytest.mark.parametrize("value", [False, 0, "false", "no", "off"])
def test_explicit_runtime_disable_values(value):
    assert configured_runtime_enabled(value) is False


def test_invalid_runtime_enable_value_fails_closed():
    with pytest.raises(ValueError, match="provider_runtime_enabled_invalid"):
        configured_runtime_enabled("sometimes")


def test_production_requires_explicit_runtime_enable(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("PROVIDER_RUNTIME_ENABLED", raising=False)
    assert configured_runtime_enabled() is False


def test_kill_switch_has_precedence():
    selection = select_provider_traffic(
        _request(),
        canary_percent=100,
        kill_switch=True,
        configured_mode_value="canary",
        runtime_enabled_value=True,
    )
    assert selection.path == ProviderTrafficPath.KILL_SWITCH
    assert selection.execute_candidate is False
    assert selection.authoritative is False


def test_kill_switch_remains_authoritative_with_invalid_lower_configuration():
    selection = select_provider_traffic(
        _request(),
        canary_percent="invalid",
        kill_switch=True,
        configured_mode_value="invalid",
        runtime_enabled_value="invalid",
    )
    assert selection.path == ProviderTrafficPath.KILL_SWITCH
    assert selection.configured_mode == "invalid"
    assert selection.canary_percent == 0
    assert selection.execute_candidate is False
    assert selection.authoritative is False


def test_disabled_runtime_never_executes_candidate_even_with_full_configuration():
    selection = select_provider_traffic(
        _request(),
        canary_percent=100,
        kill_switch=False,
        configured_mode_value="full",
        runtime_enabled_value=False,
    )
    assert selection.path == ProviderTrafficPath.CONTROL
    assert selection.reason == "provider_runtime_disabled"
    assert selection.bucket is None
    assert selection.execute_candidate is False
    assert selection.authoritative is False


def test_control_mode_never_executes_candidate():
    selection = select_provider_traffic(
        _request(),
        canary_percent=100,
        kill_switch=False,
        configured_mode_value="control",
        runtime_enabled_value=True,
    )
    assert selection.path == ProviderTrafficPath.CONTROL
    assert selection.execute_candidate is False


def test_missing_mode_defaults_to_control_even_with_full_percent(monkeypatch):
    monkeypatch.delenv("PROVIDER_RUNTIME_TRAFFIC_MODE", raising=False)
    selection = select_provider_traffic(
        _request(),
        canary_percent=100,
        kill_switch=False,
        runtime_enabled_value=True,
    )
    assert selection.configured_mode == "control"
    assert selection.path == ProviderTrafficPath.CONTROL
    assert selection.execute_candidate is False
    assert selection.authoritative is False


def test_zero_percent_canary_never_executes_candidate():
    selection = select_provider_traffic(
        _request(),
        canary_percent=0,
        kill_switch=False,
        configured_mode_value="canary",
        runtime_enabled_value=True,
    )
    assert selection.path == ProviderTrafficPath.CONTROL
    assert selection.execute_candidate is False


def test_zero_percent_shadow_never_executes_candidate():
    selection = select_provider_traffic(
        _request(),
        canary_percent=0,
        kill_switch=False,
        configured_mode_value="shadow",
        runtime_enabled_value=True,
    )
    assert selection.path == ProviderTrafficPath.CONTROL
    assert selection.execute_candidate is False
    assert selection.authoritative is False


def test_full_canary_is_authoritative():
    selection = select_provider_traffic(
        _request(),
        canary_percent=100,
        kill_switch=False,
        configured_mode_value="canary",
        runtime_enabled_value=True,
    )
    assert selection.path == ProviderTrafficPath.CANARY_AUTHORITATIVE
    assert selection.execute_candidate is True
    assert selection.authoritative is True


def test_full_mode_is_explicitly_authoritative():
    selection = select_provider_traffic(
        _request(),
        canary_percent=100,
        kill_switch=False,
        configured_mode_value="full",
        runtime_enabled_value=True,
    )
    assert selection.path == ProviderTrafficPath.CANARY_AUTHORITATIVE
    assert selection.reason == "full_mode_configured"
    assert selection.execute_candidate is True
    assert selection.authoritative is True


def test_full_mode_rejects_partial_percentage():
    with pytest.raises(ValueError, match="provider_runtime_full_percent_invalid"):
        select_provider_traffic(
            _request(),
            canary_percent=25,
            kill_switch=False,
            configured_mode_value="full",
            runtime_enabled_value=True,
        )


def test_full_shadow_executes_without_authority():
    selection = select_provider_traffic(
        _request(),
        canary_percent=100,
        kill_switch=False,
        configured_mode_value="shadow",
        runtime_enabled_value=True,
    )
    assert selection.path == ProviderTrafficPath.SHADOW_ONLY
    assert selection.execute_candidate is True
    assert selection.authoritative is False
