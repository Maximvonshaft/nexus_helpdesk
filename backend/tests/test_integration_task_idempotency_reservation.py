from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/integration_task_idempotency_reservation_tests.db",
)
os.environ.setdefault("ALLOW_DEV_AUTH", "false")
os.environ.setdefault("INTEGRATION_REQUIRE_IDEMPOTENCY_KEY", "true")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.api.integration_runtime import IntegrationTaskRequest  # noqa: E402
from app.auth_service import hash_password, hash_secret  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.enums import UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    IntegrationClient,
    IntegrationRequestLog,
    Market,
    Team,
    Tenant,
    Ticket,
    User,
)
from app.models_job_scope import (  # noqa: E402
    IntegrationClientScope,
    IntegrationRequestLogEnvelope,
)
from app.services.integration_auth import (  # noqa: E402
    AuthenticatedIntegrationClient,
    INTEGRATION_RECEIPT_SCHEMA,
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


def _tenant_fixture(
    *,
    tenant_key: str = "integration-a",
    client_key_id: str = "client-key-id",
    secret: str = "client-secret",
) -> tuple[Tenant, User, IntegrationClient]:
    with SessionLocal() as db:
        tenant = Tenant(
            tenant_key=tenant_key,
            display_name=f"Tenant {tenant_key}",
            is_active=True,
        )
        db.add(tenant)
        db.flush()
        market = Market(
            tenant_id=tenant.id,
            tenant_assignment_source="test",
            tenant_assignment_version="r15",
            code=f"{tenant_key[:6].upper()}CH",
            name=f"Market {tenant_key}",
            country_code="CH",
            is_active=True,
        )
        db.add(market)
        db.flush()
        team = Team(
            tenant_id=tenant.id,
            tenant_assignment_source="test",
            tenant_assignment_version="r15",
            name=f"Support {tenant_key}",
            team_type="support",
            market_id=market.id,
            is_active=True,
        )
        db.add(team)
        db.flush()
        user = User(
            tenant_id=tenant.id,
            tenant_assignment_source="test",
            tenant_assignment_version="r15",
            username=f"owner-{tenant_key}",
            display_name=f"Owner {tenant_key}",
            email=f"owner-{tenant_key}@example.test",
            password_hash=hash_password("pass123"),
            role=UserRole.admin,
            team_id=team.id,
            is_active=True,
        )
        db.add(user)
        db.flush()
        integration = IntegrationClient(
            name=f"integration-{tenant_key}",
            key_id=client_key_id,
            secret_hash=hash_secret(secret),
            scopes_csv="profile.read,task.write",
            rate_limit_per_minute=1000,
            is_active=True,
        )
        db.add(integration)
        db.flush()
        db.add(
            IntegrationClientScope(
                client_id=integration.id,
                scope_type="tenant",
                tenant_id=tenant.id,
                assignment_source="test",
            )
        )
        db.commit()
        return tenant, user, integration


def _headers(
    idempotency_key: str = "idem-key-1",
    *,
    key_id: str = "client-key-id",
    secret: str = "client-secret",
) -> dict[str, str]:
    return {
        "X-Client-Key-Id": key_id,
        "X-Client-Key": secret,
        "Idempotency-Key": idempotency_key,
    }


def _payload(
    contact_id: str = "+41790000001",
    tracking_number: str = "SF123456789",
) -> dict:
    return {
        "contact_id": contact_id,
        "channel": "whatsapp",
        "summary": "Customer requests manual parcel support",
        "description": (
            "Customer says the parcel was not delivered and needs human follow-up."
        ),
        "tracking_number": tracking_number,
        "priority": "normal",
        "metadata": {"source": "pytest"},
        "country_code": "CH",
    }


def _api_request_hash(payload: dict) -> str:
    return stable_request_hash(IntegrationTaskRequest(**payload).model_dump())


def _auth_client(
    client_id: int,
    tenant_id: int,
) -> AuthenticatedIntegrationClient:
    return AuthenticatedIntegrationClient(
        client_id=client_id,
        name="integration-client",
        scopes=frozenset({"profile.read", "task.write"}),
        key_id="client-key-id",
        rate_limit_per_minute=1000,
        scope_type="tenant",
        tenant_id=tenant_id,
    )


def test_begin_integration_idempotency_reserves_then_replays_safe_receipt():
    tenant, _actor, integration = _tenant_fixture()
    request_hash = stable_request_hash(_payload())

    with SessionLocal() as db:
        auth_client = _auth_client(integration.id, tenant.id)
        begin = begin_integration_idempotency(
            db,
            client=auth_client,
            endpoint="integration.task",
            method="POST",
            idempotency_key="service-reservation",
            request_hash=request_hash,
            target_tenant_id=tenant.id,
        )
        assert begin.kind == "owner"
        assert begin.row is not None
        assert begin.row.status_code is None
        assert begin.row.response_json is None
        assert db.get(IntegrationRequestLogEnvelope, begin.row.id).tenant_id == tenant.id

        processing = begin_integration_idempotency(
            db,
            client=auth_client,
            endpoint="integration.task",
            method="POST",
            idempotency_key="service-reservation",
            request_hash=request_hash,
            target_tenant_id=tenant.id,
        )
        assert processing.kind == "processing"

        response_payload = {
            "ok": True,
            "case_ref": "CS-TEST",
            "status": "created",
        }
        record_integration_response(
            db,
            client=auth_client,
            endpoint="integration.task",
            method="POST",
            idempotency_key="service-reservation",
            request_hash=request_hash,
            status_code=200,
            response_payload=response_payload,
            target_tenant_id=tenant.id,
        )

        replay = begin_integration_idempotency(
            db,
            client=auth_client,
            endpoint="integration.task",
            method="POST",
            idempotency_key="service-reservation",
            request_hash=request_hash,
            target_tenant_id=tenant.id,
        )
        assert replay.kind == "replay"
        assert replay.response_json == {
            "schema": INTEGRATION_RECEIPT_SCHEMA,
            **response_payload,
        }


def test_begin_integration_idempotency_rejects_same_key_different_payload():
    tenant, _actor, integration = _tenant_fixture()
    first_hash = stable_request_hash(_payload(contact_id="+41790000002"))
    second_hash = stable_request_hash(_payload(contact_id="+41790000003"))

    with SessionLocal() as db:
        auth_client = _auth_client(integration.id, tenant.id)
        assert begin_integration_idempotency(
            db,
            client=auth_client,
            endpoint="integration.task",
            method="POST",
            idempotency_key="service-conflict",
            request_hash=first_hash,
            target_tenant_id=tenant.id,
        ).kind == "owner"
        conflict = begin_integration_idempotency(
            db,
            client=auth_client,
            endpoint="integration.task",
            method="POST",
            idempotency_key="service-conflict",
            request_hash=second_hash,
            target_tenant_id=tenant.id,
        )
        assert conflict.kind == "conflict"
        assert (
            conflict.error_code
            == "idempotency_key_reused_with_different_payload"
        )


def test_integration_task_replay_does_not_create_second_ticket():
    tenant, _actor, _integration = _tenant_fixture()

    first = client.post(
        "/api/v1/integration/task",
        json=_payload(),
        headers=_headers("api-replay"),
    )
    second = client.post(
        "/api/v1/integration/task",
        json=_payload(),
        headers=_headers("api-replay"),
    )

    assert first.status_code == 200, first.text
    assert first.json()["status"] == "created"
    assert second.status_code == 200, second.text
    assert second.json()["idempotent"] is True
    assert second.json()["case_ref"] == first.json()["case_ref"]

    with SessionLocal() as db:
        assert (
            db.execute(
                select(func.count(Ticket.id)).where(Ticket.tenant_id == tenant.id)
            ).scalar_one()
            == 1
        )
        row = db.execute(
            select(IntegrationRequestLog).where(
                IntegrationRequestLog.idempotency_key == "api-replay"
            )
        ).scalar_one()
        stored = json.loads(row.response_json or "{}")
        assert stored["schema"] == INTEGRATION_RECEIPT_SCHEMA
        assert set(stored) <= {"schema", "ok", "case_ref", "status", "message"}
        envelope = db.get(IntegrationRequestLogEnvelope, row.id)
        assert envelope is not None
        assert envelope.tenant_id == tenant.id


def test_integration_task_processing_reservation_returns_202_without_ticket():
    tenant, _actor, integration = _tenant_fixture()
    payload = _payload(
        contact_id="+41790000004",
        tracking_number="SF987654321",
    )
    request_hash = _api_request_hash(payload)
    with SessionLocal() as db:
        row = IntegrationRequestLog(
            client_id=integration.id,
            endpoint="integration.task",
            method="POST",
            idempotency_key="api-processing",
            request_hash=request_hash,
            status_code=None,
            response_json=None,
        )
        db.add(row)
        db.flush()
        db.add(
            IntegrationRequestLogEnvelope(
                log_id=row.id,
                client_id=integration.id,
                principal_scope_type="tenant",
                tenant_id=tenant.id,
                purpose="human_support_task",
                response_schema=INTEGRATION_RECEIPT_SCHEMA,
                expires_at=row.created_at,
            )
        )
        db.commit()

    response = client.post(
        "/api/v1/integration/task",
        json=payload,
        headers=_headers("api-processing"),
    )

    assert response.status_code == 202, response.text
    assert response.json()["error_code"] == "request_processing"
    with SessionLocal() as db:
        assert db.execute(select(func.count(Ticket.id))).scalar_one() == 0


def test_integration_task_same_key_different_payload_returns_409_without_duplicate():
    _tenant_fixture()
    first_payload = _payload(
        contact_id="+41790000005",
        tracking_number="SF111111111",
    )
    second_payload = _payload(
        contact_id="+41790000006",
        tracking_number="SF222222222",
    )

    first = client.post(
        "/api/v1/integration/task",
        json=first_payload,
        headers=_headers("api-conflict"),
    )
    second = client.post(
        "/api/v1/integration/task",
        json=second_payload,
        headers=_headers("api-conflict"),
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 409, second.text
    assert (
        second.json()["error_code"]
        == "idempotency_key_reused_with_different_payload"
    )
    with SessionLocal() as db:
        assert db.execute(select(func.count(Ticket.id))).scalar_one() == 1
