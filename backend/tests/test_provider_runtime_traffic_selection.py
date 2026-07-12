import pytest

from app.services.provider_runtime.schemas import ProviderRequest
from app.services.provider_runtime.traffic_selection import (
    ProviderTrafficPath,
    configured_traffic_mode,
    effective_canary_percent,
    effective_kill_switch,
    persisted_traffic_configuration_errors,
    safe_traffic_configuration,
    select_provider_traffic,
    stable_canary_bucket,
)

_BUCKET_CONTRACT = "sha256(tenant_id,tenant_key,channel_key,session_id,scenario)%100"


def _request(session_id: str = "session-1") -> ProviderRequest:
    return ProviderRequest(
        request_id="request-1",
        tenant_id="tenant-1",
        tenant_key="tenant-key-1",
        channel_key="website",
        session_id=session_id,
        scenario="webchat_runtime_reply",
        body="hello",
        output_contract="nexus_webchat_runtime_reply_v1",
        timeout_ms=1000,
    )


def _request_for_bucket(target: int) -> ProviderRequest:
    for index in range(10000):
        candidate = _request(f"session-{index}")
        if stable_canary_bucket(candidate) == target:
            return candidate
    raise AssertionError(f"bucket {target} not found")


def test_missing_traffic_configuration_defaults_to_control_zero(monkeypatch):
    for name in (
        "PROVIDER_RUNTIME_TRAFFIC_MODE",
        "PROVIDER_RUNTIME_CANARY_PERCENT",
        "PROVIDER_RUNTIME_KILL_SWITCH",
    ):
        monkeypatch.delenv(name, raising=False)

    summary = safe_traffic_configuration()
    decision = select_provider_traffic(
        _request(),
        canary_percent=summary["canary_percent"],
        kill_switch=summary["kill_switch"],
        configured_mode_value=summary["configured_mode"],
    )

    assert summary["configured_mode"] == "control"
    assert summary["canary_percent"] == 0
    assert summary["kill_switch"] is False
    assert summary["configuration_errors"] == []
    assert decision.path == ProviderTrafficPath.CONTROL
    assert decision.execute_candidate is False
    assert decision.authoritative is False


def test_zero_percent_is_control_and_does_not_execute_candidate():
    decision = select_provider_traffic(
        _request(),
        canary_percent=0,
        kill_switch=False,
        configured_mode_value="canary",
    )
    assert decision.path == ProviderTrafficPath.CONTROL
    assert not decision.execute_candidate
    assert not decision.authoritative
    assert decision.reason == "canary_percent_zero"


def test_one_five_and_twenty_five_percent_use_strict_deterministic_bucket_boundary():
    for percent in (1, 5, 25):
        selected = select_provider_traffic(
            _request_for_bucket(percent - 1),
            canary_percent=percent,
            kill_switch=False,
            configured_mode_value="canary",
        )
        excluded = select_provider_traffic(
            _request_for_bucket(percent),
            canary_percent=percent,
            kill_switch=False,
            configured_mode_value="canary",
        )
        assert selected.path == ProviderTrafficPath.CANARY_AUTHORITATIVE
        assert selected.authoritative
        assert excluded.path == ProviderTrafficPath.CONTROL
        assert not excluded.execute_candidate


def test_one_hundred_percent_selects_every_bucket():
    for bucket in (0, 25, 50, 99):
        decision = select_provider_traffic(
            _request_for_bucket(bucket),
            canary_percent=100,
            kill_switch=False,
            configured_mode_value="canary",
        )
        assert decision.path == ProviderTrafficPath.CANARY_AUTHORITATIVE
        assert decision.authoritative


def test_bucket_and_decision_are_stable_after_request_reconstruction():
    before_restart = _request("stable-session")
    after_restart = ProviderRequest(**before_restart.model_dump())

    assert before_restart is not after_restart
    assert stable_canary_bucket(before_restart) == stable_canary_bucket(after_restart)
    assert select_provider_traffic(
        before_restart,
        canary_percent=25,
        kill_switch=False,
        configured_mode_value="canary",
    ) == select_provider_traffic(
        after_restart,
        canary_percent=25,
        kill_switch=False,
        configured_mode_value="canary",
    )


def test_bucket_contract_declares_every_hashed_scope_field():
    decision = select_provider_traffic(
        _request(),
        canary_percent=25,
        kill_switch=False,
        configured_mode_value="canary",
    )
    assert decision.safe_summary()["bucket_contract"] == _BUCKET_CONTRACT
    assert safe_traffic_configuration()["bucket_contract"] == _BUCKET_CONTRACT


def test_shadow_executes_candidate_but_is_never_authoritative():
    decision = select_provider_traffic(
        _request(),
        canary_percent=100,
        kill_switch=False,
        configured_mode_value="shadow",
    )
    assert decision.path == ProviderTrafficPath.SHADOW_ONLY
    assert decision.execute_candidate
    assert not decision.authoritative


def test_control_mode_suppresses_candidate_even_at_one_hundred_percent():
    decision = select_provider_traffic(
        _request(),
        canary_percent=100,
        kill_switch=False,
        configured_mode_value="control",
    )
    assert decision.path == ProviderTrafficPath.CONTROL
    assert not decision.execute_candidate


def test_kill_switch_overrides_shadow_and_canary():
    decision = select_provider_traffic(
        _request(),
        canary_percent=100,
        kill_switch=True,
        configured_mode_value="shadow",
    )
    assert decision.path == ProviderTrafficPath.KILL_SWITCH
    assert not decision.execute_candidate
    assert not decision.authoritative


def test_valid_kill_switch_overrides_invalid_lower_priority_mode_and_percent():
    decision = select_provider_traffic(
        _request(),
        canary_percent="invalid",
        kill_switch=True,
        configured_mode_value="invalid",
    )
    assert decision.path == ProviderTrafficPath.KILL_SWITCH
    assert decision.canary_percent == 0
    assert decision.configured_mode == "invalid"
    assert decision.configuration_errors == (
        "provider_runtime_canary_percent_invalid",
        "provider_runtime_traffic_mode_invalid",
    )
    assert not decision.execute_candidate
    assert not decision.authoritative


@pytest.mark.parametrize("value", ["unknown", "", "   "])
def test_invalid_or_explicitly_empty_mode_fails_closed(value):
    with pytest.raises(ValueError, match="provider_runtime_traffic_mode_invalid"):
        configured_traffic_mode(value)


@pytest.mark.parametrize("value", ["abc", "-1", "101", "1.5"])
def test_invalid_canary_override_fails_closed(monkeypatch, value):
    monkeypatch.setenv("PROVIDER_RUNTIME_CANARY_PERCENT", value)
    with pytest.raises(ValueError, match="provider_runtime_canary_percent_invalid"):
        effective_canary_percent(25)


@pytest.mark.parametrize("value", [-1, 101, 1.5, "01", True])
def test_invalid_database_or_direct_canary_value_fails_closed(monkeypatch, value):
    monkeypatch.delenv("PROVIDER_RUNTIME_CANARY_PERCENT", raising=False)
    with pytest.raises(ValueError, match="provider_runtime_canary_percent_invalid"):
        effective_canary_percent(value)
    with pytest.raises(ValueError, match="provider_runtime_canary_percent_invalid"):
        select_provider_traffic(
            _request(),
            canary_percent=value,
            kill_switch=False,
            configured_mode_value="canary",
        )


@pytest.mark.parametrize("value", ["maybe", "enabled", "2", ""])
def test_invalid_kill_switch_override_fails_closed(monkeypatch, value):
    monkeypatch.setenv("PROVIDER_RUNTIME_KILL_SWITCH", value)
    with pytest.raises(ValueError, match="provider_runtime_kill_switch_invalid"):
        effective_kill_switch(False)


@pytest.mark.parametrize("value", ["false", 0, 1, None])
def test_invalid_database_or_direct_kill_switch_value_fails_closed(monkeypatch, value):
    monkeypatch.delenv("PROVIDER_RUNTIME_KILL_SWITCH", raising=False)
    with pytest.raises(ValueError, match="provider_runtime_kill_switch_invalid"):
        effective_kill_switch(value)
    with pytest.raises(ValueError, match="provider_runtime_kill_switch_invalid"):
        select_provider_traffic(
            _request(),
            canary_percent=25,
            kill_switch=value,
            configured_mode_value="canary",
        )


def test_persisted_configuration_errors_are_independent_of_env_overrides(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_CANARY_PERCENT", "5")
    monkeypatch.setenv("PROVIDER_RUNTIME_KILL_SWITCH", "true")

    errors = persisted_traffic_configuration_errors(
        canary_percent=101,
        kill_switch="false",
    )

    assert errors == [
        "provider_runtime_canary_percent_invalid",
        "provider_runtime_kill_switch_invalid",
    ]


def test_safe_configuration_reports_invalid_database_default(monkeypatch):
    monkeypatch.delenv("PROVIDER_RUNTIME_CANARY_PERCENT", raising=False)
    summary = safe_traffic_configuration(default_canary_percent=101, default_kill_switch=False)
    assert summary["default_canary_percent"] is None
    assert summary["canary_percent"] is None
    assert summary["configuration_errors"] == ["provider_runtime_canary_percent_invalid"]


def test_safe_configuration_reports_invalid_kill_switch_default(monkeypatch):
    monkeypatch.delenv("PROVIDER_RUNTIME_KILL_SWITCH", raising=False)
    summary = safe_traffic_configuration(default_canary_percent=25, default_kill_switch="false")
    assert summary["default_kill_switch"] is None
    assert summary["kill_switch"] is None
    assert summary["configuration_errors"] == ["provider_runtime_kill_switch_invalid"]


def test_safe_configuration_reports_invalid_database_default_even_when_env_overrides_it(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_CANARY_PERCENT", "5")
    summary = safe_traffic_configuration(default_canary_percent=101, default_kill_switch=False)
    assert summary["default_canary_percent"] is None
    assert summary["canary_percent"] == 5
    assert summary["configuration_errors"] == ["provider_runtime_canary_percent_invalid"]


def test_safe_configuration_reports_all_malformed_overrides(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE", "unknown")
    monkeypatch.setenv("PROVIDER_RUNTIME_CANARY_PERCENT", "abc")
    monkeypatch.setenv("PROVIDER_RUNTIME_KILL_SWITCH", "maybe")

    summary = safe_traffic_configuration(default_canary_percent=25, default_kill_switch=False)

    assert summary["configured_mode"] == "invalid"
    assert summary["canary_percent"] is None
    assert summary["kill_switch"] is None
    assert summary["configuration_errors"] == [
        "provider_runtime_traffic_mode_invalid",
        "provider_runtime_canary_percent_invalid",
        "provider_runtime_kill_switch_invalid",
    ]
