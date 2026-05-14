from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import webchat_fast_rate_limit as rate_limit

pytestmark = pytest.mark.fast_lane_v2_2_2


def _settings(**overrides):
    values = {
        "trusted_proxy_cidrs": ("127.0.0.1/32", "172.16.0.0/12"),
        "rate_limit_trust_x_forwarded_for": True,
        "rate_limit_window_seconds": 60,
        "rate_limit_max_requests": 30,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _request(remote: str, xff: str | None = None):
    headers = {}
    if xff is not None:
        headers["x-forwarded-for"] = xff
    return SimpleNamespace(client=SimpleNamespace(host=remote), headers=headers)


def test_trusted_proxy_uses_rightmost_untrusted_public_xff(monkeypatch):
    monkeypatch.setattr(rate_limit, "get_webchat_fast_settings", lambda: _settings())

    request = _request("172.16.0.10", "8.8.8.8, 1.1.1.1, 172.16.0.10")

    assert rate_limit.trusted_client_ip(request) == "1.1.1.1"


def test_spoofed_leftmost_xff_cannot_rotate_bucket_identity(monkeypatch):
    monkeypatch.setattr(rate_limit, "get_webchat_fast_settings", lambda: _settings())

    first = _request("172.16.0.10", "8.8.8.8, 1.1.1.1")
    second = _request("172.16.0.10", "9.9.9.9, 1.1.1.1")

    assert rate_limit.trusted_client_ip(first) == "1.1.1.1"
    assert rate_limit.trusted_client_ip(second) == "1.1.1.1"


def test_untrusted_remote_ignores_xff(monkeypatch):
    monkeypatch.setattr(rate_limit, "get_webchat_fast_settings", lambda: _settings())

    request = _request("8.8.4.4", "1.1.1.1")

    assert rate_limit.trusted_client_ip(request) == "8.8.4.4"


def test_xff_can_be_disabled_by_config(monkeypatch):
    monkeypatch.setattr(rate_limit, "get_webchat_fast_settings", lambda: _settings(rate_limit_trust_x_forwarded_for=False))

    request = _request("172.16.0.10", "1.1.1.1")

    assert rate_limit.trusted_client_ip(request) == "172.16.0.10"
