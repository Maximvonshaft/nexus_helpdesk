import pytest

from app.services.provider_runtime.schemas import ProviderRequest
from app.services.provider_runtime.traffic_selection import (
    ProviderTrafficPath,
    configured_traffic_mode,
    effective_canary_percent,
    effective_kill_switch,
    safe_traffic_configuration,
    select_provider_traffic,
    stable_canary_bucket,
)


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


def test_bucket_is_stable_across_repeated_evaluation():
    item = _request("stable-session")
    assert stable_canary_bucket(item) == stable_canary_bucket(item)
    assert select_provider_traffic(
        item,
        canary_percent=25,
        kill_switch=False,
        configured_mode_value="canary",
    ) == select_provider_traffic(
        item,
        canary_percent=25,
        kill_switch=False,
        configured_mode_value="canary",
    )


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


def test_invalid_mode_fails_closed(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE", "unknown")
    with pytest.raises(ValueError, match="provider_runtime_traffic_mode_invalid"):
        configured_traffic_mode()


@pytest.mark.parametrize("value", ["abc", "-1", "101", "1.5"])
def test_invalid_canary_override_fails_closed(monkeypatch, value):
    monkeypatch.setenv("PROVIDER_RUNTIME_CANARY_PERCENT", value)
    with pytest.raises(ValueError, match="provider_runtime_canary_percent_invalid"):
        effective_canary_percent(25)


@pytest.mark.parametrize("value", ["maybe", "enabled", "2", ""])
def test_invalid_kill_switch_override_fails_closed(monkeypatch, value):
    monkeypatch.setenv("PROVIDER_RUNTIME_KILL_SWITCH", value)
    with pytest.raises(ValueError, match="provider_runtime_kill_switch_invalid"):
        effective_kill_switch(False)


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
