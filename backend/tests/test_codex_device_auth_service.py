from __future__ import annotations

from unittest.mock import Mock

import pytest

from app.services.provider_runtime import codex_device_auth_service as service_module
from app.services.provider_runtime.codex_device_auth_service import CodexDeviceAuthService


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")
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


class _Result:
    def __init__(self, first_value=None):
        self._first_value = first_value

    def mappings(self):
        return self

    def first(self):
        return self._first_value


class _OpenClawPendingPollClient:
    payloads: list[dict] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, *args, **kwargs):
        assert url == "https://auth.openai.com/api/accounts/deviceauth/token"
        self.__class__.payloads.append(kwargs.get("json"))
        return _FakeResponse({"error": "authorization_pending"}, status_code=403)


class _OpenClawCompletePollClient:
    calls: list[tuple[str, dict]] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, *args, **kwargs):
        if url == "https://auth.openai.com/api/accounts/deviceauth/token":
            self.__class__.calls.append((url, kwargs.get("json")))
            return _FakeResponse({
                "authorization_code": "authorization-code-1",
                "code_verifier": "code-verifier-1",
            })
        if url == "https://auth.openai.com/oauth/token":
            self.__class__.calls.append((url, kwargs.get("data")))
            return _FakeResponse({
                "access_token": "access-token-1",
                "refresh_token": "refresh-token-1",
                "expires_in": 3600,
                "account_id": "account-1",
                "email": "operator@example.test",
                "chatgpt_plan_type": "plus",
                "scope": "openid profile email offline_access",
            })
        raise AssertionError(f"unexpected url {url}")


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


@pytest.mark.asyncio
async def test_codex_device_poll_openclaw_payload_pending(monkeypatch):
    _OpenClawPendingPollClient.payloads = []
    monkeypatch.setenv("CODEX_OAUTH_DEVICE_FLOW_ENABLED", "true")
    monkeypatch.setenv("CODEX_OAUTH_AUTH_BASE_URL", "https://auth.openai.com")
    monkeypatch.setenv("CODEX_OAUTH_CLIENT_ID", "app_EMoamEEZ73f0CkXaXp7hrann")
    monkeypatch.setenv("CODEX_OAUTH_DEVICE_USERCODE_PATH", "/api/accounts/deviceauth/usercode")
    monkeypatch.setenv("CODEX_OAUTH_DEVICE_TOKEN_PATH", "/api/accounts/deviceauth/token")
    monkeypatch.setenv("CODEX_OAUTH_TOKEN_PATH", "/oauth/token")
    monkeypatch.setattr(service_module.httpx, "AsyncClient", _OpenClawPendingPollClient)

    session = {
        "id": "session-1",
        "device_auth_id": "device-auth-1",
        "user_code": "ABCD-EFGH",
        "status": "pending",
        "expires_at": None,
        "created_by": "user-1",
        "scope": "openid profile email offline_access",
    }
    mock_db = Mock()
    mock_db.execute.return_value = _Result(session)

    svc = CodexDeviceAuthService(mock_db, Mock())
    res = await svc.poll_device_flow("tenant_1", "session-1")

    assert res == {"status": "pending", "error_code": "authorization_pending"}
    assert _OpenClawPendingPollClient.payloads == [{
        "device_auth_id": "device-auth-1",
        "user_code": "ABCD-EFGH",
    }]
    mock_db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_codex_device_poll_openclaw_exchanges_with_device_callback_redirect(monkeypatch):
    _OpenClawCompletePollClient.calls = []
    monkeypatch.setenv("CODEX_OAUTH_DEVICE_FLOW_ENABLED", "true")
    monkeypatch.setenv("CODEX_OAUTH_AUTH_BASE_URL", "https://auth.openai.com")
    monkeypatch.setenv("CODEX_OAUTH_CLIENT_ID", "app_EMoamEEZ73f0CkXaXp7hrann")
    monkeypatch.setenv("CODEX_OAUTH_DEVICE_USERCODE_PATH", "/api/accounts/deviceauth/usercode")
    monkeypatch.setenv("CODEX_OAUTH_DEVICE_TOKEN_PATH", "/api/accounts/deviceauth/token")
    monkeypatch.setenv("CODEX_OAUTH_TOKEN_PATH", "/oauth/token")
    monkeypatch.setattr(service_module.httpx, "AsyncClient", _OpenClawCompletePollClient)

    session = {
        "id": "session-1",
        "device_auth_id": "device-auth-1",
        "user_code": "ABCD-EFGH",
        "status": "pending",
        "expires_at": None,
        "created_by": "user-1",
        "scope": "openid profile email offline_access",
    }
    mock_db = Mock()
    mock_db.execute.side_effect = [
        _Result(session),
        _Result(("credential-1",)),
        _Result(),
    ]
    mock_crypto = Mock()
    mock_crypto.encrypt.side_effect = lambda value: f"enc:{value}" if value else None
    mock_crypto.get_safe_fingerprint.return_value = "fingerprint-1"

    svc = CodexDeviceAuthService(mock_db, mock_crypto)
    res = await svc.poll_device_flow("tenant_1", "session-1")

    assert res == {"status": "authorized", "credential_id": "credential-1"}
    assert _OpenClawCompletePollClient.calls[0] == (
        "https://auth.openai.com/api/accounts/deviceauth/token",
        {"device_auth_id": "device-auth-1", "user_code": "ABCD-EFGH"},
    )
    token_payload = _OpenClawCompletePollClient.calls[1][1]
    assert token_payload["grant_type"] == "authorization_code"
    assert token_payload["code"] == "authorization-code-1"
    assert token_payload["client_id"] == "app_EMoamEEZ73f0CkXaXp7hrann"
    assert token_payload["redirect_uri"] == "https://auth.openai.com/deviceauth/callback"
    assert token_payload["code_verifier"] == "code-verifier-1"
    assert mock_db.commit.call_count == 2
