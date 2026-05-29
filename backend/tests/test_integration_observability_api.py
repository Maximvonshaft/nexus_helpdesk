from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models as _models  # noqa: E402,F401
from app.auth_service import create_access_token  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import IntegrationClient, IntegrationRequestLog, User  # noqa: E402
from app.settings import get_settings  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    get_settings.cache_clear()
    db_file = tmp_path / "integration_observability.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False}, future=True)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
        get_settings.cache_clear()


@pytest.fixture()
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _user(db_session, *, user_id: int, role: UserRole) -> User:
    user = User(
        id=user_id,
        username=f"integration-observability-{user_id}",
        display_name="Integration Observability User",
        email=f"integration-observability-{user_id}@example.test",
        password_hash="test",
        role=role,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def _seed_integration_logs(db_session) -> IntegrationClient:
    integration_client = IntegrationClient(
        name="Warehouse API",
        key_id="wh-api-key",
        secret_hash="not-returned",
        scopes_csv="profile.read,task.write",
        is_active=True,
    )
    db_session.add(integration_client)
    db_session.flush()
    db_session.add_all([
        IntegrationRequestLog(
            client_id=integration_client.id,
            endpoint="integration.profile",
            method="GET",
            idempotency_key=None,
            request_hash="profile-hash",
            status_code=200,
            response_json='{"request_id":"req-profile-1","secret":"hidden","nested":{"authorization":"Bearer hidden"}}',
        ),
        IntegrationRequestLog(
            client_id=integration_client.id,
            endpoint="integration.task",
            method="POST",
            idempotency_key="task-key-1",
            request_hash="task-hash",
            status_code=503,
            error_code="upstream_timeout",
            response_json='{"detail":{"requestId":"req-task-1"},"token":"hidden"}',
        ),
        IntegrationRequestLog(
            client_id=integration_client.id,
            endpoint="integration.task",
            method="POST",
            idempotency_key="task-key-conflict",
            request_hash="task-conflict-hash",
            status_code=409,
            error_code="idempotency_key_reused_with_different_payload",
            response_json='{"request_id":"req-conflict-1"}',
        ),
    ])
    db_session.commit()
    return integration_client


def test_integration_observability_lists_scope_request_id_retryability_and_redacted_payload(client: TestClient, db_session):
    admin = _user(db_session, user_id=9711, role=UserRole.admin)
    integration_client = _seed_integration_logs(db_session)

    response = client.get("/api/admin/integration-observability/requests?limit=20", headers=_headers(admin))
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 3
    assert payload["summary"]["by_status"]["success"] == 1
    assert payload["summary"]["by_status"]["retryable"] == 1
    assert payload["summary"]["by_status"]["conflict"] == 1
    assert payload["summary"]["retryable"] == 1
    assert payload["summary"]["error_codes"] == ["idempotency_key_reused_with_different_payload", "upstream_timeout"]
    assert payload["summary"]["clients"] == ["Warehouse API"]

    success = next(item for item in payload["items"] if item["status_bucket"] == "success")
    assert success["client_id"] == integration_client.id
    assert success["client_name"] == "Warehouse API"
    assert success["client_key_id"] == "wh-api-key"
    assert success["scopes"] == ["profile.read", "task.write"]
    assert success["request_id"] == "req-profile-1"
    assert success["retryable"] is False
    assert success["response_preview"]["secret"] == "[redacted]"
    assert success["response_preview"]["nested"]["authorization"] == "[redacted]"

    retryable = next(item for item in payload["items"] if item["status_bucket"] == "retryable")
    assert retryable["request_id"] == "req-task-1"
    assert retryable["error_code"] == "upstream_timeout"
    assert retryable["retryable"] is True
    assert retryable["response_preview"]["token"] == "[redacted]"

    filtered = client.get(
        f"/api/admin/integration-observability/requests?client_id={integration_client.id}&status_bucket=retryable&q=req-task-1",
        headers=_headers(admin),
    )
    assert filtered.status_code == 200, filtered.text
    filtered_payload = filtered.json()
    assert filtered_payload["total"] == 1
    assert filtered_payload["items"][0]["idempotency_key"] == "task-key-1"
    assert filtered_payload["filters"]["status_bucket"] == "retryable"


def test_integration_observability_requires_runtime_manage(client: TestClient, db_session):
    agent = _user(db_session, user_id=9712, role=UserRole.agent)
    _seed_integration_logs(db_session)

    response = client.get("/api/admin/integration-observability/requests", headers=_headers(agent))
    assert response.status_code == 403
