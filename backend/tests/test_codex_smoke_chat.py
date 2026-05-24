from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_current_user
from app.db import Base, get_db
from app.enums import UserRole
from app.main import app
from app.models import AdminAuditLog, User, UserCapabilityOverride
from app.services.provider_runtime.credential_crypto import CredentialCryptoService


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'codex-smoke.db'}", future=True, connect_args={"check_same_thread": False})
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE provider_credentials (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                provider_runtime TEXT NOT NULL,
                credential_type TEXT NOT NULL,
                profile_id TEXT NOT NULL,
                account_id TEXT,
                email TEXT,
                chatgpt_plan_type TEXT,
                encrypted_access_token TEXT,
                encrypted_refresh_token TEXT,
                expires_at TIMESTAMP,
                status TEXT NOT NULL,
                token_fingerprint TEXT,
                created_by TEXT,
                scope TEXT,
                last_used_at TIMESTAMP,
                last_refresh_at TIMESTAMP,
                last_error_code TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                revoked_at TIMESTAMP
            )
        """))
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)


@pytest.fixture()
def users(db_session):
    admin = User(username="admin", display_name="Admin", email="admin@example.com", password_hash="x", role=UserRole.admin)
    agent = User(username="agent", display_name="Agent", email="agent@example.com", password_hash="x", role=UserRole.agent)
    db_session.add_all([admin, agent])
    db_session.commit()
    return admin, agent


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    monkeypatch.delenv("CODEX_LLM_ENDPOINT", raising=False)
    monkeypatch.delenv("CODEX_LLM_API_STYLE", raising=False)
    monkeypatch.delenv("CODEX_LLM_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("CODEX_LLM_RETRIES", raising=False)
    monkeypatch.delenv("CODEX_LLM_MODEL", raising=False)
    monkeypatch.delenv("CODEX_SMOKE_ENDPOINT", raising=False)
    monkeypatch.delenv("CODEX_SMOKE_MODEL", raising=False)
    monkeypatch.delenv("CODEX_SMOKE_TIMEOUT_MS", raising=False)
    monkeypatch.delenv("CODEX_APP_SERVER_BRIDGE_URL", raising=False)
    monkeypatch.delenv("CODEX_APP_SERVER_LOGIN_URL", raising=False)
    monkeypatch.delenv("CODEX_APP_SERVER_TOKEN", raising=False)
    monkeypatch.delenv("CODEX_APP_SERVER_TOKEN_FILE", raising=False)
    monkeypatch.delenv("CODEX_REPLY_BRIDGE_TOKEN_FILE", raising=False)
    monkeypatch.delenv("CODEX_APP_SERVER_TIMEOUT_MS", raising=False)
    monkeypatch.delenv("CODEX_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("CODEX_OAUTH_TOKEN_URL", raising=False)


@pytest.fixture()
def client(db_session, users):
    admin, _agent = users

    def override_db():
        yield db_session

    def override_user():
        return admin

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.clear()


def _insert_credential(db_session, *, access_token: str = "opaque-access-value", expires_at=None, refresh_token: str | None = "opaque-refresh-value"):
    crypto = CredentialCryptoService()
    now = datetime.now(timezone.utc)
    db_session.execute(text("""
        INSERT INTO provider_credentials
        (id, tenant_id, provider, provider_runtime, credential_type, profile_id, account_id, chatgpt_plan_type,
         encrypted_access_token, encrypted_refresh_token, expires_at, status, token_fingerprint, created_by, scope, created_at, updated_at)
        VALUES
        ('cred-1', 'default', 'openai-codex', 'codex_app_server', 'oauth', 'profile-1', 'acct-1', 'plus',
         :access, :refresh, :expires_at, 'active', 'fingerprint', '1', 'openid profile', :now, :now)
    """), {
        "access": crypto.encrypt(access_token),
        "refresh": crypto.encrypt(refresh_token) if refresh_token else None,
        "expires_at": expires_at,
        "now": now,
    })
    db_session.commit()


def _post(client: TestClient, nonce: str = "nonce-123"):
    return client.post("/api/admin/provider-credentials/codex/smoke-chat", json={"prompt": "smoke only", "nonce": nonce, "mode": "smoke"})


def test_smoke_chat_requires_authentication(db_session):
    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    try:
        response = TestClient(app, raise_server_exceptions=False).post(
            "/api/admin/provider-credentials/codex/smoke-chat",
            json={"prompt": "smoke", "mode": "smoke"},
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 401


def test_smoke_chat_requires_admin_runtime_manage(db_session, users):
    admin, _agent = users
    db_session.add(UserCapabilityOverride(user_id=admin.id, capability="runtime.manage", allowed=False))
    db_session.commit()

    def override_db():
        yield db_session

    def override_user():
        return admin

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        response = TestClient(app, raise_server_exceptions=False).post(
            "/api/admin/provider-credentials/codex/smoke-chat",
            json={"prompt": "smoke", "mode": "smoke"},
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 403


def test_smoke_chat_no_credential_safe_failure(client):
    response = _post(client)
    assert response.status_code == 404
    body = response.json()["detail"]
    assert body["reason"] == "codex_credential_not_found"
    assert "access" not in json.dumps(body).lower()


def test_smoke_chat_credential_present_endpoint_not_configured(client, db_session):
    _insert_credential(db_session)
    response = _post(client)
    assert response.status_code == 503
    body = response.json()["detail"]
    assert body["reason"] == "codex_llm_endpoint_not_configured"
    assert body["credential_status"] == "authorized"


def test_smoke_chat_provider_returns_nonce(client, db_session, monkeypatch):
    _insert_credential(db_session)
    monkeypatch.setenv("CODEX_LLM_ENDPOINT", "https://codex-smoke.internal/chat")
    captured: dict = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "ok nonce-abc"}}]}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json, headers):
            captured["url"] = url
            captured["payload"] = json
            captured["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr("app.services.provider_runtime.codex_llm_client.httpx.AsyncClient", FakeAsyncClient)
    response = _post(client, nonce="nonce-abc")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["nonce_echoed"] is True
    assert body["model_call_status"] == "completed"
    assert body["request_id"]
    assert captured["url"] == "https://codex-smoke.internal/chat"
    assert captured["headers"]["Authorization"].split(" ", 1) == ["Bearer", "opaque-access-value"]
    response_blob = json.dumps(body).lower()
    assert "opaque-access-value" not in response_blob
    assert "authorization" not in response_blob


def test_smoke_chat_provider_without_nonce_reports_false(client, db_session, monkeypatch):
    _insert_credential(db_session)
    monkeypatch.setenv("CODEX_LLM_ENDPOINT", "https://codex-smoke.internal/chat")

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"reply": "ordinary model response"}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json, headers):
            return FakeResponse()

    monkeypatch.setattr("app.services.provider_runtime.codex_llm_client.httpx.AsyncClient", FakeAsyncClient)
    response = _post(client, nonce="nonce-missing")
    assert response.status_code == 200
    assert response.json()["nonce_echoed"] is False


def test_smoke_chat_provider_error_is_safe_502(client, db_session, monkeypatch):
    _insert_credential(db_session)
    monkeypatch.setenv("CODEX_LLM_ENDPOINT", "https://codex-smoke.internal/chat")

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json, headers):
            raise httpx.ConnectError("connect failed")

    monkeypatch.setattr("app.services.provider_runtime.codex_llm_client.httpx.AsyncClient", FakeAsyncClient)
    response = _post(client)
    assert response.status_code == 502
    body = response.json()["detail"]
    assert body["reason"] == "codex_provider_call_failed"
    assert "opaque-access-value" not in json.dumps(body)


def test_smoke_chat_refresh_required_is_safe_409(client, db_session, monkeypatch):
    _insert_credential(db_session, expires_at=datetime.now(timezone.utc) - timedelta(minutes=10), refresh_token=None)
    monkeypatch.setenv("CODEX_LLM_ENDPOINT", "https://codex-smoke.internal/chat")
    response = _post(client)
    assert response.status_code == 409
    body = response.json()["detail"]
    assert body["reason"] == "credential_refresh_required"
    assert "opaque-access-value" not in json.dumps(body)


def test_smoke_chat_response_redacts_secret_markers(client, db_session, monkeypatch):
    _insert_credential(db_session)
    monkeypatch.setenv("CODEX_LLM_ENDPOINT", "https://codex-smoke.internal/chat")

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"text": "nonce-redact access_token " + "Bearer " + "abcdefghijklmnop " + "sk-" + "abcdefghi"}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json, headers):
            return FakeResponse()

    monkeypatch.setattr("app.services.provider_runtime.codex_llm_client.httpx.AsyncClient", FakeAsyncClient)
    response = _post(client, nonce="nonce-redact")
    assert response.status_code == 200
    text = response.json()["response_text_redacted"]
    assert "access_token" not in text
    assert "Bearer " + "abcdefghijklmnop" not in text
    assert "sk-" + "abcdefghi" not in text


def test_smoke_chat_audit_uses_hashes_not_raw_prompt(client, db_session, monkeypatch):
    _insert_credential(db_session)
    monkeypatch.setenv("CODEX_LLM_ENDPOINT", "https://codex-smoke.internal/chat")

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"text": "nonce-audit"}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json, headers):
            return FakeResponse()

    monkeypatch.setattr("app.services.provider_runtime.codex_llm_client.httpx.AsyncClient", FakeAsyncClient)
    response = client.post(
        "/api/admin/provider-credentials/codex/smoke-chat",
        json={"prompt": "sensitive operator prompt", "nonce": "nonce-audit", "mode": "smoke"},
    )
    assert response.status_code == 200
    audit = db_session.query(AdminAuditLog).filter(AdminAuditLog.action == "codex_smoke_chat_invoked").order_by(AdminAuditLog.id.desc()).first()
    assert audit is not None
    audit_blob = audit.new_value_json or ""
    assert "sensitive operator prompt" not in audit_blob
    assert "nonce-audit" not in audit_blob
    assert "opaque-access-value" not in audit_blob
    assert "prompt_hash" in audit_blob


def test_smoke_chat_codex_app_server_bridge_uses_existing_runtime(client, db_session, monkeypatch):
    _insert_credential(db_session)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("CODEX_APP_SERVER_BRIDGE_URL", "http://127.0.0.1:18794/reply")
    monkeypatch.setenv("CODEX_APP_SERVER_LOGIN_URL", "http://127.0.0.1:18794/login")
    monkeypatch.setenv("CODEX_APP_SERVER_TOKEN", "bridge-shared")
    calls: list[dict] = []

    class FakeResponse:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json, headers):
            calls.append({"url": url, "payload": json, "headers": headers})
            if url.endswith("/login"):
                return FakeResponse({"ok": True})
            return FakeResponse({"reply": "bridge said nonce-bridge"})

    monkeypatch.setattr("app.services.provider_runtime.codex_llm_client.httpx.AsyncClient", FakeAsyncClient)
    response = _post(client, nonce="nonce-bridge")

    assert response.status_code == 200
    assert response.json()["nonce_echoed"] is True
    assert [call["url"] for call in calls] == ["http://127.0.0.1:18794/login", "http://127.0.0.1:18794/reply"]
    assert calls[0]["headers"]["Authorization"].split(" ", 1) == ["Bearer", "bridge-shared"]
    assert calls[0]["payload"]["login"]["accessToken"] == "opaque-access-value"
    assert calls[0]["payload"]["login"]["chatgptAccountId"] == "acct-1"
    assert "login" not in calls[1]["payload"]
    assert "opaque-access-value" not in json.dumps(response.json())


def test_smoke_chat_provider_unauthorized_is_safe_409(client, db_session, monkeypatch):
    _insert_credential(db_session)
    monkeypatch.setenv("CODEX_LLM_ENDPOINT", "https://codex-smoke.internal/chat")

    class FakeResponse:
        status_code = 401

        def raise_for_status(self):
            raise httpx.HTTPStatusError("unauthorized", request=httpx.Request("POST", "https://codex-smoke.internal/chat"), response=httpx.Response(401))

        def json(self):
            return {"error": "denied"}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json, headers):
            return FakeResponse()

    monkeypatch.setattr("app.services.provider_runtime.codex_llm_client.httpx.AsyncClient", FakeAsyncClient)
    response = _post(client)
    assert response.status_code == 409
    body = response.json()["detail"]
    assert body["reason"] == "credential_refresh_required"
    assert "opaque-access-value" not in json.dumps(body)


def test_smoke_chat_provider_timeout_is_safe_502(client, db_session, monkeypatch):
    _insert_credential(db_session)
    monkeypatch.setenv("CODEX_LLM_ENDPOINT", "https://codex-smoke.internal/chat")

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json, headers):
            raise httpx.TimeoutException("timed out")

    monkeypatch.setattr("app.services.provider_runtime.codex_llm_client.httpx.AsyncClient", FakeAsyncClient)
    response = _post(client)
    assert response.status_code == 502
    assert response.json()["detail"]["reason"] == "codex_provider_call_failed"
