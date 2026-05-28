from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/admin_integration_observability_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.auth_service import create_access_token, hash_password, hash_secret  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AdminAuditLog, IntegrationClient, IntegrationRequestLog, User  # noqa: E402

client = TestClient(app, raise_server_exceptions=False)


def setup_function():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def teardown_function():
    Base.metadata.drop_all(engine)


def _make_user(role: UserRole, username: str) -> User:
    with SessionLocal() as db:
        row = User(
            username=username,
            display_name=username.title(),
            email=f"{username}@example.test",
            password_hash=hash_password("pass123"),
            role=role,
            is_active=True,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row


def _auth_headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def _make_integration_client() -> IntegrationClient:
    with SessionLocal() as db:
        row = IntegrationClient(
            name="ops-client",
            key_id="ops-client-key",
            secret_hash=hash_secret("ops-client-secret"),
            scopes_csv="profile.read,task.write",
            rate_limit_per_minute=100,
            is_active=True,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row


def _seed_logs(integration_client: IntegrationClient) -> None:
    with SessionLocal() as db:
        db.add_all([
            IntegrationRequestLog(
                client_id=integration_client.id,
                endpoint="integration.profile",
                method="GET",
                request_id="req-profile-ok",
                status_code=200,
                response_json=json.dumps({
                    "ok": True,
                    "customer": {"name": "Private Customer", "email": "customer@example.test", "phone": "+41790000001"},
                    "api_key": "should-not-render",
                }),
            ),
            IntegrationRequestLog(
                client_id=integration_client.id,
                endpoint="integration.task",
                method="POST",
                idempotency_key="idem-conflict",
                request_hash="a" * 64,
                request_id="req-task-conflict",
                status_code=409,
                error_code="idempotency_key_reused_with_different_payload",
                response_json=json.dumps({"ok": False, "error_code": "idempotency_key_reused_with_different_payload"}),
            ),
            IntegrationRequestLog(
                client_id=integration_client.id,
                endpoint="integration.task",
                method="POST",
                idempotency_key="idem-rate",
                request_hash="b" * 64,
                request_id="req-task-rate-limited",
                status_code=429,
                error_code="rate_limited",
                response_json=json.dumps({"ok": False, "detail": "Integration rate limit exceeded"}),
            ),
        ])
        db.commit()


def test_integration_observability_requires_runtime_manage():
    agent = _make_user(UserRole.agent, "agent")

    response = client.get("/api/admin/integration-observability", headers=_auth_headers(agent))

    assert response.status_code == 403


def test_integration_observability_summarizes_real_request_logs_and_redacts_payloads():
    admin = _make_user(UserRole.admin, "admin")
    integration_client = _make_integration_client()
    _seed_logs(integration_client)

    response = client.get(
        "/api/admin/integration-observability?status=all&limit=10",
        headers=_auth_headers(admin),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["summary"]["total"] == 3
    assert body["summary"]["success_count"] == 1
    assert body["summary"]["error_count"] == 2
    assert body["summary"]["retryable_count"] == 1
    assert body["summary"]["idempotency_conflict_count"] == 1
    assert body["summary"]["rate_limited_count"] == 1
    assert body["capabilities"]["request_id_persisted"] is True
    assert body["capabilities"]["client_registration_api"] is False
    assert body["capabilities"]["latency_available"] is False
    assert any(contract["path"] == "/api/v1/integration/task" for contract in body["contracts"])

    conflict = next(item for item in body["items"] if item["request_id"] == "req-task-conflict")
    assert conflict["request_hash_present"] is True
    assert conflict["idempotency_key_present"] is True
    assert conflict["retryable"] is False

    profile = next(item for item in body["items"] if item["endpoint"] == "integration.profile")
    assert "customer@example.test" not in profile["response_preview"]
    assert "+41790000001" not in profile["response_preview"]
    assert "should-not-render" not in profile["response_preview"]


def test_integration_task_persists_request_id_header_in_request_log():
    _make_user(UserRole.admin, "integration-owner")
    _make_integration_client()

    response = client.post(
        "/api/v1/integration/task",
        json={
            "contact_id": "+41790000002",
            "channel": "whatsapp",
            "summary": "Customer needs manual parcel support",
            "description": "Customer says the parcel was not delivered.",
            "tracking_number": "SF123456789",
            "priority": "normal",
            "metadata": {"source": "pytest"},
            "country_code": "CH",
        },
        headers={
            "X-Client-Key-Id": "ops-client-key",
            "X-Client-Key": "ops-client-secret",
            "Idempotency-Key": "idem-request-id",
            "X-Request-Id": "req-integration-task-123",
        },
    )

    assert response.status_code == 200
    with SessionLocal() as db:
        row = db.query(IntegrationRequestLog).filter_by(idempotency_key="idem-request-id").one()
        assert row.request_id == "req-integration-task-123"


def test_integration_observability_csv_export_writes_admin_audit():
    admin = _make_user(UserRole.admin, "admin")
    integration_client = _make_integration_client()
    _seed_logs(integration_client)

    response = client.get(
        "/api/admin/integration-observability/export.csv?status=retryable&limit=20",
        headers=_auth_headers(admin),
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "request_hash_present" in response.text
    assert "bbbb" not in response.text
    with SessionLocal() as db:
        audit = db.query(AdminAuditLog).filter_by(action="integration_observability.export_csv").one()
        assert audit.actor_id == admin.id
        assert '"row_count": 1' in audit.new_value_json
