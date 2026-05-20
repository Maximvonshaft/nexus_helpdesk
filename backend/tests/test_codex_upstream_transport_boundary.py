from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import httpx


REPO_ROOT = Path(__file__).resolve().parents[2]
TRANSPORT_PATH = REPO_ROOT / "tools" / "codex-reply-bridge" / "upstream_transport_boundary.py"

spec = importlib.util.spec_from_file_location("codex_upstream_transport_boundary", TRANSPORT_PATH)
assert spec is not None
transport = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = transport
assert spec.loader is not None
spec.loader.exec_module(transport)


def test_validate_private_app_server_url_accepts_loopback_http():
    value, error = transport.validate_private_app_server_url("http://127.0.0.1:18795")

    assert value == "http://127.0.0.1:18795"
    assert error is None


def test_validate_private_app_server_url_accepts_docker_bridge_private_http():
    value, error = transport.validate_private_app_server_url("http://172.18.0.1:18793")

    assert value == "http://172.18.0.1:18793"
    assert error is None


def test_validate_private_app_server_url_accepts_tailnet_cgnat_http():
    value, error = transport.validate_private_app_server_url("http://100.106.75.61:18792")

    assert value == "http://100.106.75.61:18792"
    assert error is None


def test_validate_private_app_server_url_rejects_public_http():
    value, error = transport.validate_private_app_server_url("http://93.184.216.34")

    assert value is None
    assert error == "app_server_url_must_be_private"


def test_validate_private_app_server_url_rejects_userinfo():
    value, error = transport.validate_private_app_server_url("https://user:pass@127.0.0.1:18795")

    assert value is None
    assert error == "app_server_base_url_userinfo_forbidden"


def test_login_start_rejects_missing_url():
    result = asyncio.run(
        transport.post_account_login_start(
            settings=transport.TransportBoundarySettings(app_server_base_url=None),
            login_payload={"type": "apiKey", "apiKey": "sample-value"},
        )
    )

    assert result.ok is False
    assert result.error_code == "app_server_base_url_missing"
    assert result.safe_summary["endpoint_path"] == "account/login/start"
    assert "sample-value" not in str(result.safe_summary)


def test_login_start_rejects_invalid_payload():
    result = asyncio.run(
        transport.post_account_login_start(
            settings=transport.TransportBoundarySettings(app_server_base_url="http://127.0.0.1:18795"),
            login_payload={"type": "bad"},
        )
    )

    assert result.ok is False
    assert result.error_code == "login_payload_invalid"


def test_login_start_success_uses_account_login_start_and_safe_summary(monkeypatch):
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"sessionId": "session-1", "status": "ok"}

    class FakeAsyncClient:
        def __init__(self, timeout, follow_redirects):
            captured["timeout"] = timeout
            captured["follow_redirects"] = follow_redirects

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, endpoint, headers, json):
            captured["endpoint"] = endpoint
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    result = asyncio.run(
        transport.post_account_login_start(
            settings=transport.TransportBoundarySettings(app_server_base_url="http://127.0.0.1:18795", timeout_ms=1234),
            login_payload={"type": "apiKey", "apiKey": "sample-value"},
        )
    )

    assert result.ok is True
    assert result.status_code == 200
    assert captured["endpoint"] == "http://127.0.0.1:18795/account/login/start"
    assert captured["json"] == {"type": "apiKey", "apiKey": "sample-value"}
    assert captured["headers"]["x-nexus-transport-boundary"] == "codex-upstream-v1"  # type: ignore[index]
    assert result.safe_summary["response_keys"] == ["sessionId", "status"]
    assert "sample-value" not in str(result.safe_summary)


def test_login_start_http_error_is_safe(monkeypatch):
    class FakeResponse:
        status_code = 503

        def json(self):
            return {"message": "unavailable"}

    class FakeAsyncClient:
        def __init__(self, timeout, follow_redirects):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, endpoint, headers, json):
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    result = asyncio.run(
        transport.post_account_login_start(
            settings=transport.TransportBoundarySettings(app_server_base_url="http://127.0.0.1:18795"),
            login_payload={"type": "apiKey", "apiKey": "sample-value"},
        )
    )

    assert result.ok is False
    assert result.status_code == 503
    assert result.error_code == "app_server_login_http_error"
    assert "sample-value" not in str(result.safe_summary)
