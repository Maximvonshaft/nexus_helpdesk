from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/admin_action_rate_limit_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.auth_service import create_access_token, hash_password  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.enums import JobStatus, UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AdminActionRateLimitBucket, AdminAuditLog, BackgroundJob, User  # noqa: E402
from app.services import admin_action_rate_limit as rate_limit_service  # noqa: E402
from app.api import admin as admin_api  # noqa: E402
from app.api import admin_queue as admin_queue_api  # noqa: E402


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def tighten_limits(monkeypatch):
    monkeypatch.setattr(rate_limit_service.settings, "admin_action_rate_limit_enabled", True)
    monkeypatch.setattr(rate_limit_service.settings, "admin_action_rate_limit_window_seconds", 60)
    monkeypatch.setattr(rate_limit_service.settings, "admin_action_rate_limit_single_max", 1)
    monkeypatch.setattr(rate_limit_service.settings, "admin_action_rate_limit_batch_max", 1)
    monkeypatch.setattr(rate_limit_service.settings, "admin_action_rate_limit_consume_once_max", 1)
    monkeypatch.setattr(admin_api.settings, "admin_action_rate_limit_single_max", 1)
    monkeypatch.setattr(admin_api.settings, "admin_action_rate_limit_consume_once_max", 1)
    monkeypatch.setattr(admin_queue_api.settings, "admin_action_rate_limit_single_max", 1)
    monkeypatch.setattr(admin_queue_api.settings, "admin_action_rate_limit_batch_max", 1)


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(admin_api, "consume_openclaw_events_once", lambda db: 0)
    return TestClient(app, raise_server_exceptions=False)


def _make_user(username: str, role: UserRole = UserRole.admin) -> User:
    with SessionLocal() as db:
        user = User(
            username=username,
            display_name=username,
            email=f"{username}@example.test",
            password_hash=hash_password("pass123"),
            role=role,
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def test_rate_limit_response_contains_request_id_and_audit_log(client, caplog):
    admin = _make_user("admin-rate")
    caplog.set_level(logging.WARNING, logger="nexusdesk")

    first = client.post("/api/admin/openclaw/events/consume-once", headers=_headers(admin))
    second = client.post("/api/admin/openclaw/events/consume-once", headers=_headers(admin))

    assert first.status_code == 200
    assert second.status_code == 429
    payload = second.json()
    assert payload["detail"]["action"] == "openclaw.events.consume_once"
    assert payload["detail"]["request_id"]

    with SessionLocal() as db:
        audit = db.query(AdminAuditLog).filter(AdminAuditLog.action == "admin_action.rate_limited").one()
        assert audit.actor_id == admin.id
        assert db.query(AdminActionRateLimitBucket).filter(AdminActionRateLimitBucket.bucket_key == f"{admin.id}:openclaw.events.consume_once").one().request_count == 1

    assert any(record.message == "admin_action_rate_limited" for record in caplog.records)


def test_different_action_keys_are_counted_independently(client):
    admin = _make_user("admin-actions")

    first = client.post("/api/admin/jobs/requeue-dead", headers=_headers(admin))
    second = client.post("/api/admin/openclaw/events/consume-once", headers=_headers(admin))
    third = client.post("/api/admin/jobs/requeue-dead", headers=_headers(admin))

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
    assert third.json()["detail"]["action"] == "background_job.requeue_dead_batch"


def test_different_users_have_independent_buckets(client):
    admin_a = _make_user("admin-a")
    admin_b = _make_user("admin-b")

    first = client.post("/api/admin/openclaw/events/consume-once", headers=_headers(admin_a))
    second = client.post("/api/admin/openclaw/events/consume-once", headers=_headers(admin_a))
    third = client.post("/api/admin/openclaw/events/consume-once", headers=_headers(admin_b))

    assert first.status_code == 200
    assert second.status_code == 429
    assert third.status_code == 200


def test_requeue_endpoint_is_guarded_by_rate_limit(client):
    admin = _make_user("admin-requeue")
    with SessionLocal() as db:
        job = BackgroundJob(
            queue_name="tests",
            job_type="unit",
            payload_json="{}",
            dedupe_key=None,
            status=JobStatus.dead,
            attempt_count=3,
            max_attempts=3,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = job.id

    first = client.post(f"/api/admin/jobs/{job_id}/requeue", headers=_headers(admin))
    second = client.post(f"/api/admin/jobs/{job_id}/requeue", headers=_headers(admin))

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["detail"]["action"] == "background_job.requeue"
