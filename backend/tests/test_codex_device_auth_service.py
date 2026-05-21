from __future__ import annotations

from unittest.mock import Mock

import pytest

from app.services.provider_runtime import codex_device_auth_service as service_module
from app.services.provider_runtime.codex_device_auth_service import CodexDeviceAuthService


class _FakeResponse:
    status_code = 200

    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, *args, **kwargs):
        assert url == "https://auth.example.test/device/usercode"
        return _FakeResponse({
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://auth.example.test/activate",
            "device_code": "device-code-1",
            "interval": 5,
            "expires_in": 900,
        })


@pytest.mark.asyncio
async def test_codex_device_auth_service(monkeypatch):
    monkeypatch.setenv("CODEX_OAUTH_DEVICE_FLOW_ENABLED", "true")
    monkeypatch.setenv("CODEX_OAUTH_AUTH_BASE_URL", "https://auth.example.test")
    monkeypatch.setenv("CODEX_OAUTH_CLIENT_ID", "client-test")
    monkeypatch.setenv("CODEX_OAUTH_DEVICE_USERCODE_PATH", "/device/usercode")
    monkeypatch.setenv("CODEX_OAUTH_DEVICE_TOKEN_PATH", "/device/token")
    monkeypatch.setenv("CODEX_OAUTH_TOKEN_PATH", "/oauth/token")
    monkeypatch.setattr(service_module.httpx, "AsyncClient", _FakeAsyncClient)

    mock_db = Mock()
    mock_crypto = Mock()

    svc = CodexDeviceAuthService(mock_db, mock_crypto)
    res = await svc.start_device_flow("tenant_1", "user_1")

    assert res["user_code"] == "ABCD-EFGH"
    assert res["verification_url"] == "https://auth.example.test/activate"
    assert res["session_id"]
    assert res["interval"] == 5
    mock_db.execute.assert_called_once()
    mock_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_codex_device_auth_service_disabled(monkeypatch):
    monkeypatch.setenv("CODEX_OAUTH_DEVICE_FLOW_ENABLED", "false")
    mock_db = Mock()
    mock_crypto = Mock()

    svc = CodexDeviceAuthService(mock_db, mock_crypto)
    with pytest.raises(ValueError, match="Device flow is disabled"):
        await svc.start_device_flow("tenant_1", "user_1")
