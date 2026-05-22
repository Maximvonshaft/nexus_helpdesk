from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_current_user
from app.db import Base, get_db
from app.enums import UserRole
from app.main import app
from app.models import AdminAuditLog, User
from app.services.provider_runtime.credential_crypto import CredentialCryptoService


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _jwt(payload: dict) -> str:
    header = _b64url(json.dumps({"alg": "none", "typ": "JWT"}).encode("utf-8"))
    body = _b64url(json.dumps(payload).encode("utf-8"))
    return f"{header}.{body}.signature"


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'manual-flow.db'}", future=True, connect_args={"check_same_thread": False})
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE provider_auth_sessions (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                flow_type TEXT NOT NULL,
                state TEXT,
                code_verifier TEXT,
                nonce TEXT,
                redirect_uri TEXT,
                scope TEXT,
                expires_at TIMESTAMP,
                status TEXT NOT NULL,
                error_code TEXT,
                user_code TEXT,
                verification_url TEXT,
                created_by TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP
            )
        """))
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
        conn.execute(text("""
            CREATE UNIQUE INDEX uq_provider_credentials_active_profile
            ON provider_credentials (tenant_id, provider, profile_id)
            WHERE revoked_at IS NULL
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


def _start(client: TestClient) -> dict:
    response = client.post("/api/admin/provider-credentials/codex/manual/start")
    assert response.status_code == 200
    return response.json()


def test_manual_start_generates_openclaw_authorization_url(client):
    data = _start(client)
    parsed = urlparse(data["authorization_url"])
    params = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "auth.openai.com"
    assert parsed.path == "/oauth/authorize"
    assert params["response_type"] == ["code"]
    assert params["client_id"] == ["app_EMoamEEZ73f0CkXaXp7hrann"]
    assert params["redirect_uri"] == ["http://localhost:1455/auth/callback"]
    assert params["scope"] == ["openid profile email offline_access"]
    assert params["state"] == [data["state"]]
    assert params["code_challenge_method"] == ["S256"]
    assert params["code_challenge"][0]
    assert params["originator"] == ["openclaw"]
    assert params["codex_cli_simplified_flow"] == ["true"]
    assert params["id_token_add_organizations"] == ["true"]
    assert data["redirect_uri"] == "http://localhost:1455/auth/callback"


def test_manual_complete_full_redirect_url_parses_code_state(client, monkeypatch):
    captured: dict = {}
    access = _jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "acct_123"}})

    class FakeResponse:
        def raise_for_status(self):  # noqa: D401
            return None

        def json(self):
            return {"access_token": access, "refresh_token": "refresh_secret", "expires_in": 3600}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, data, headers):
            captured["url"] = url
            captured["data"] = dict(data)
            captured["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr("app.services.provider_runtime.codex_credential_broker.httpx.AsyncClient", FakeAsyncClient)
    started = _start(client)
    complete = client.post(
        "/api/admin/provider-credentials/codex/manual/complete",
        json={
            "session_id": started["session_id"],
            "authorization_response": f"http://localhost:1455/auth/callback?code=code_abc&state={started['state']}",
        },
    )

    assert complete.status_code == 200
    body = complete.json()
    assert body["status"] == "authorized"
    assert "access_token" not in body
    assert "refresh_token" not in body
    assert captured["url"] == "https://auth.openai.com/oauth/token"
    assert captured["data"]["grant_type"] == "authorization_code"
    assert captured["data"]["code"] == "code_abc"
    assert captured["data"]["client_id"] == "app_EMoamEEZ73f0CkXaXp7hrann"
    assert captured["data"]["redirect_uri"] == "http://localhost:1455/auth/callback"
    assert captured["data"]["code_verifier"]
    assert "client_secret" not in captured["data"]


def test_manual_complete_state_mismatch_rejects(client):
    started = _start(client)
    complete = client.post(
        "/api/admin/provider-credentials/codex/manual/complete",
        json={
            "session_id": started["session_id"],
            "authorization_response": "http://localhost:1455/auth/callback?code=code_abc&state=wrong",
        },
    )
    assert complete.status_code == 400
    assert "state mismatch" in complete.json()["detail"]


def test_manual_complete_expired_session_rejects(client, db_session):
    started = _start(client)
    db_session.execute(
        text("UPDATE provider_auth_sessions SET expires_at = :expires_at WHERE id = :id"),
        {"expires_at": datetime.now(timezone.utc) - timedelta(minutes=1), "id": started["session_id"]},
    )
    db_session.commit()

    complete = client.post(
        "/api/admin/provider-credentials/codex/manual/complete",
        json={"session_id": started["session_id"], "authorization_response": "code_abc"},
    )
    assert complete.status_code == 400
    assert "expired" in complete.json()["detail"]


def test_token_exchange_success_encrypts_tokens_and_masks_response(client, db_session, monkeypatch):
    access = _jwt({
        "https://api.openai.com/auth": {"chatgpt_account_id": "acct_456", "chatgpt_plan_type": "plus"},
        "https://api.openai.com/profile": {"email": "user@example.com"},
    })

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"access_token": access, "refresh_token": "refresh_secret_2", "expires_in": 7200}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, data, headers):
            return FakeResponse()

    monkeypatch.setattr("app.services.provider_runtime.codex_credential_broker.httpx.AsyncClient", FakeAsyncClient)
    started = _start(client)
    complete = client.post(
        "/api/admin/provider-credentials/codex/manual/complete",
        json={"session_id": started["session_id"], "authorization_response": f"code=code_abc&state={started['state']}"},
    )

    assert complete.status_code == 200
    body = complete.json()
    assert "access_token" not in json.dumps(body)
    assert "refresh_token" not in json.dumps(body)

    row = db_session.execute(text("SELECT * FROM provider_credentials WHERE provider = 'openai-codex'")).mappings().one()
    crypto = CredentialCryptoService()
    assert row["account_id"] == "acct_456"
    assert row["email"] == "user@example.com"
    assert row["chatgpt_plan_type"] == "plus"
    assert row["encrypted_access_token"] != access
    assert row["encrypted_refresh_token"] != "refresh_secret_2"
    assert crypto.decrypt(row["encrypted_access_token"]) == access
    assert crypto.decrypt(row["encrypted_refresh_token"]) == "refresh_secret_2"


def test_user_without_runtime_manage_gets_403(db_session, users):
    _admin, agent = users

    def override_db():
        yield db_session

    def override_user():
        return agent

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    try:
        response = TestClient(app, raise_server_exceptions=False).post("/api/admin/provider-credentials/codex/manual/start")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 403


def test_manual_routes_are_registered():
    paths = {route.path for route in app.routes}
    assert "/api/admin/provider-credentials/codex/manual/start" in paths
    assert "/api/admin/provider-credentials/codex/manual/complete" in paths


def test_audit_actions_written(client, db_session, monkeypatch):
    access = _jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "acct_audit"}})

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"access_token": access, "refresh_token": "refresh_audit", "expires_in": 3600}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, data, headers):
            return FakeResponse()

    monkeypatch.setattr("app.services.provider_runtime.codex_credential_broker.httpx.AsyncClient", FakeAsyncClient)
    started = _start(client)
    client.post(
        "/api/admin/provider-credentials/codex/manual/complete",
        json={"session_id": started["session_id"], "authorization_response": f"code=code_abc&state={started['state']}"},
    )
    actions = {row.action for row in db_session.query(AdminAuditLog).all()}
    assert "codex_oauth_manual_started" in actions
    assert "codex_oauth_manual_completed" in actions
