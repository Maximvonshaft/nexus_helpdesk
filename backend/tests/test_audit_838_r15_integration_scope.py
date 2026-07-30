from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/audit-838-r15-integration-scope.db",
)
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.auth_service import hash_secret  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.enums import (  # noqa: E402
    SourceChannel,
    TicketPriority,
    TicketSource,
    TicketStatus,
)
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Customer,
    IntegrationClient,
    IntegrationRequestLog,
    Tenant,
    Ticket,
)
from app.models_integration_scope import IntegrationClientScope  # noqa: E402

client = TestClient(app, raise_server_exceptions=False)


def setup_function():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def teardown_function():
    Base.metadata.drop_all(engine)


def _tenant(db, key: str) -> Tenant:
    row = Tenant(
        tenant_key=key,
        display_name=key,
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def _client(db, *, tenant: Tenant | None, key_id: str) -> IntegrationClient:
    row = IntegrationClient(
        name=key_id,
        key_id=key_id,
        secret_hash=hash_secret("integration-secret-value"),
        scopes_csv="profile.read,task.write",
        rate_limit_per_minute=1000,
        is_active=True,
    )
    db.add(row)
    db.flush()
    if tenant is not None:
        db.add(
            IntegrationClientScope(
                client_id=row.id,
                scope_type="tenant",
                tenant_id=tenant.id,
                assignment_source="pytest",
                assignment_version="r15",
            )
        )
        db.flush()
    return row


def _headers(key_id: str) -> dict[str, str]:
    return {
        "X-Client-Key-Id": key_id,
        "X-Client-Key": "integration-secret-value",
    }


def test_tenant_client_cannot_read_other_tenant_profile_and_log_contains_no_pii():
    with SessionLocal() as db:
        tenant_a = _tenant(db, "integration-a")
        tenant_b = _tenant(db, "integration-b")
        _client(db, tenant=tenant_a, key_id="tenant-a-client")
        customer_b = Customer(
            tenant_id=tenant_b.id,
            tenant_assignment_source="pytest",
            tenant_assignment_version="r15",
            name="Tenant B Customer",
            phone="+41795550000",
            phone_normalized="+41795550000",
            email="tenant-b@example.test",
            email_normalized="tenant-b@example.test",
            external_ref="tenant-b-ref",
        )
        db.add(customer_b)
        db.flush()
        db.add(
            Ticket(
                tenant_id=tenant_b.id,
                tenant_assignment_source="pytest",
                tenant_assignment_version="r15",
                ticket_no="R15-INTEGRATION-B",
                title="Tenant B private case",
                description="Must never be returned to Tenant A",
                customer_id=customer_b.id,
                source=TicketSource.api,
                source_channel=SourceChannel.whatsapp,
                priority=TicketPriority.high,
                status=TicketStatus.in_progress,
            )
        )
        db.commit()

    response = client.get(
        "/api/v1/integration/profile/+41795550000",
        headers=_headers("tenant-a-client"),
    )
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "found": False,
        "message": "No customer profile found for this contact.",
        "channel": "whatsapp",
    }

    with SessionLocal() as db:
        row = (
            db.query(IntegrationRequestLog)
            .filter(IntegrationRequestLog.endpoint == "integration.profile")
            .order_by(IntegrationRequestLog.id.desc())
            .first()
        )
        assert row is not None
        persisted = json.loads(row.response_json or "{}")
        assert persisted == {
            "schema": "nexus.integration-profile-log.v2",
            "ok": True,
            "found": False,
            "pii_persisted": False,
        }
        rendered = json.dumps(persisted, sort_keys=True)
        assert "+41795550000" not in rendered
        assert "tenant-b@example.test" not in rendered
        assert "Tenant B private case" not in rendered


def test_unscoped_historical_integration_client_fails_closed():
    with SessionLocal() as db:
        _client(db, tenant=None, key_id="unscoped-client")
        db.commit()

    response = client.get(
        "/api/v1/integration/profile/unknown",
        headers=_headers("unscoped-client"),
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "integration_principal_scope_required"
