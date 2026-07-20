from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/integration_task_idempotency_reservation_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.api.integration_runtime import IntegrationTaskRequest  # noqa: E402
from app.auth_service import hash_password, hash_secret  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import IntegrationClient, IntegrationRequestLog, Ticket, User  # noqa: E402
from app.services.integration_auth import (  # noqa: E402
    AuthenticatedIntegrationClient,
    begin_integration_idempotency,
    record_integration_response,
    stable_request_hash,
)

client = TestClient(app, raise_server_exceptions=False)


def setup_function():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def teardown_function():
    Base.metadata.drop_all(engine)


def _make_actor() -> User:
    with SessionLocal() as db:
        user = User(
            username="integration-owner",
            display_name="Integration Owner",
            email="integration-owner@example.test",
            password_hash=hash_password("pass123"),
            role=UserRole.admin,
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def _make_integration_client() -> IntegrationClient:
    with SessionLocal() as db:
        row = IntegrationClient(
            name="integration-client",
            key_id="client-key-id",
            secret_hash=hash_secret("client-secret"),
            scopes_csv="profile.read,task.write",
            rate_limit_per_minute=1000,
            is_active=True,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row


def _headers(idempotency_key: str = "idem-key-1") -> dict[str, str]:
    return {
        "X-Client-Key-Id": "client-key-id",
        "X-Client-Key": "client-secret",
        "Idempotency-Key": idempotency_key,
    }


def _payload(contact_id: str = "+41790000001", tracking_number: str = "SF123456789") -> dict:
    return {
        "contact_id": contact_id,
        "channel": "whatsapp",
        "summary": "Customer requests manual parcel support",
        "description": "Customer says the parcel was not delivered and needs human follow-up.",
        "tracking_number": tracking_number,
        "priority": "normal",
        "metadata": {"source": "pytest"},
        "country_code": "CH",
    }


def _api_request_hash(payload: dict) -> str:
    return stable_request_hash(IntegrationTaskRequest(**payload).model_dump())


def _auth_client(client_id: int) -> AuthenticatedIntegrationClient:
    return AuthenticatedIntegrationClient(
        client_id=client_id,
        name="integration-client",
        scopes={"profile.read", "task.write"},
        key_id="client-key-id",
        rate_limit_per_minute=1000,
    )


def test_begin_integration_idempotency_reserves_then_replays_response():
    integration_client = _make_integration_client()
    request_hash = stable_request_hash(_payload())

    with SessionLocal() as db:
        auth_client = _auth_client(integration_client.id)
        begin = begin_integration_idempotency(
            db,
            client=auth_client,
            endpoint="integration.task",
            method="POST",
            idempotency_key="service-reservation",
            request_hash=request_hash,
        )
        assert begin.kind == "owner"
        assert begin.row is not None
        assert begin.row.status_code is None
        assert begin.row.response_json is None

        processing = begin_integration_idempotency(
            db,
            client=auth_client,
            endpoint="integration.task",
            method="POST",
            idempotency_key="service-reservation",
            request_hash=request_hash,
        )
        assert processing.kind == "processing"

        response_payload = {"ok": True, "case_ref": "CS-TEST", "status": "created"}
        record_integration_response(
            db,
            client=auth_client,
            endpoint="integration.task",
            method="POST",
            idempotency_key="service-reservation",
            request_hash=request_hash,
            status_code=200,
            response_payload=response_payload,
        )

        replay = begin_integration_idempotency(
            db,
            client=auth_client,
            endpoint="integration.task",
            method="POST",
            idempotency_key="service-reservation",
            request_hash=request_hash,
        )
        assert replay.kind == "replay"
        assert replay.response_json == response_payload


def test_begin_integration_idempotency_rejects_same_key_different_payload():
    integration_client = _make_integration_client()
    first_hash = stable_request_hash(_payload(contact_id="+41790000002"))
    second_hash = stable_request_hash(_payload(contact_id="+41790000003"))

    with SessionLocal() as db:
        auth_client = _auth_client(integration_client.id)
        assert begin_integration_idempotency(
            db,
            client=auth_client,
            endpoint="integration.task",
            method="POST",
            idempotency_key="service-conflict",
            request_hash=first_hash,
        ).kind == "owner"
        conflict = begin_integration_idempotency(
            db,
            client=auth_client,
            endpoint="integration.task",
            method="POST",
            idempotency_key="service-conflict",
            request_hash=second_hash,
        )
        assert conflict.kind == "conflict"
        assert conflict.error_code == "idempotency_key_reused_with_different_payload"


def test_integration_task_replay_does_not_create_second_ticket():
    _make_actor()
    _make_integration_client()

    first = client.post("/api/v1/integration/task", json=_payload(), headers=_headers("api-replay"))
    second = client.post("/api/v1/integration/task", json=_payload(), headers=_headers("api-replay"))

    assert first.status_code == 200
    assert first.json()["status"] == "created"
    assert second.status_code == 200
    assert second.json()["idempotent"] is True
    assert second.json()["case_ref"] == first.json()["case_ref"]

    with SessionLocal() as db:
        assert db.execute(select(func.count(Ticket.id))).scalar_one() == 1
        row = db.execute(select(IntegrationRequestLog).where(IntegrationRequestLog.idempotency_key == "api-replay")).scalar_one()
        assert row.status_code == 200
        assert row.response_json


def test_integration_task_processing_reservation_returns_202_without_ticket():
    _make_actor()
    integration_client = _make_integration_client()
    payload = _payload(contact_id="+41790000004", tracking_number="SF987654321")
    request_hash = _api_request_hash(payload)
    with SessionLocal() as db:
        db.add(
            IntegrationRequestLog(
                client_id=integration_client.id,
                endpoint="integration.task",
                method="POST",
                idempotency_key="api-processing",
                request_hash=request_hash,
                status_code=None,
                response_json=None,
            )
        )
        db.commit()

    response = client.post("/api/v1/integration/task", json=payload, headers=_headers("api-processing"))

    assert response.status_code == 202
    assert response.json()["error_code"] == "request_processing"
    with SessionLocal() as db:
        assert db.execute(select(func.count(Ticket.id))).scalar_one() == 0


def test_integration_task_same_key_different_payload_returns_409_without_ticket():
    _make_actor()
    _make_integration_client()
    first_payload = _payload(contact_id="+41790000005", tracking_number="SF111111111")
    second_payload = _payload(contact_id="+41790000006", tracking_number="SF222222222")

    first = client.post("/api/v1/integration/task", json=first_payload, headers=_headers("api-conflict"))
    second = client.post("/api/v1/integration/task", json=second_payload, headers=_headers("api-conflict"))

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["error_code"] == "idempotency_key_reused_with_different_payload"
    with SessionLocal() as db:
        assert db.execute(select(func.count(Ticket.id))).scalar_one() == 1
