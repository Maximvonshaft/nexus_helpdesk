from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
import pytest

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/openclaw_bridge_resilience_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.services.openclaw_client_factory import OpenClawBridgeHTTPClient, OpenClawBridgeHTTPError  # noqa: E402


def test_bridge_http_success_uses_injected_pooled_client() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"ok": True, "messages": [{"id": "m1", "text": "hello"}]})

    pooled = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://bridge.local")
    client = OpenClawBridgeHTTPClient(bridge_url="http://bridge.local", client=pooled)

    assert client.messages_read("session-1") == [{"id": "m1", "text": "hello"}]
    assert len(calls) == 1
    assert calls[0].url.path == "/read-messages"
    assert not pooled.is_closed


def test_bridge_timeout_returns_safe_degraded_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("token=secret-value timed out", request=request)

    pooled = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://bridge.local")
    client = OpenClawBridgeHTTPClient(bridge_url="http://bridge.local", client=pooled)

    with pytest.raises(OpenClawBridgeHTTPError, match="bridge_timeout"):
        client.messages_read("session-1")


def test_bridge_invalid_json_does_not_expose_response_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json-with-token=secret-value")

    pooled = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://bridge.local")
    client = OpenClawBridgeHTTPClient(bridge_url="http://bridge.local", client=pooled)

    with pytest.raises(OpenClawBridgeHTTPError) as exc_info:
        client.messages_read("session-1")

    assert str(exc_info.value) == "bridge_invalid_json"
    assert "secret-value" not in str(exc_info.value)
