from __future__ import annotations

import pytest

from app.services.provider_runtime.schemas import ProviderRequest
from app.services.provider_runtime.traffic_selection import (
    ProviderTrafficPath,
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


def test_kill_switch_has_precedence():
    selection = select_provider_traffic(
        _request(),
        canary_percent=100,
        kill_switch=True,
        configured_mode_value="canary",
    )
    assert selection.path == ProviderTrafficPath.KILL_SWITCH
    assert selection.execute_candidate is False
    assert selection.authoritative is False


def test_control_mode_never_executes_candidate():
    selection = select_provider_traffic(
        _request(),
        canary_percent=100,
        kill_switch=False,
        configured_mode_value="control",
    )
    assert selection.path == ProviderTrafficPath.CONTROL
    assert selection.execute_candidate is False


def test_zero_percent_canary_never_executes_candidate():
    selection = select_provider_traffic(
        _request(),
        canary_percent=0,
        kill_switch=False,
        configured_mode_value="canary",
    )
    assert selection.path == ProviderTrafficPath.CONTROL
    assert selection.execute_candidate is False


def test_full_canary_is_authoritative():
    selection = select_provider_traffic(
        _request(),
        canary_percent=100,
        kill_switch=False,
        configured_mode_value="canary",
    )
    assert selection.path == ProviderTrafficPath.CANARY_AUTHORITATIVE
    assert selection.execute_candidate is True
    assert selection.authoritative is True


def test_shadow_executes_without_authority():
    selection = select_provider_traffic(
        _request(),
        canary_percent=25,
        kill_switch=False,
        configured_mode_value="shadow",
    )
    assert selection.path == ProviderTrafficPath.SHADOW_ONLY
    assert selection.execute_candidate is True
    assert selection.authoritative is False
