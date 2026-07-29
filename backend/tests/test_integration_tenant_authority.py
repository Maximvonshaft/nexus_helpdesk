from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/integration_tenant_authority_tests.db",
)
os.environ.setdefault("ALLOW_DEV_AUTH", "false")
os.environ.setdefault("INTEGRATION_REQUIRE_IDEMPOTENCY_KEY", "true")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.auth_service import hash_password, hash_secret  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.enums import (  # noqa: E402
    SourceChannel,
    TicketPriority,
    TicketSource,
    TicketStatus,
    UserRole,
)
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Customer,
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

client = TestClient(app, raise_server_exceptions=False)


def setup_function():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


def teardown_function():
    Base.metadata.drop_all(engine)


def _tenant(
    *,
    key: str,
    client_key: str,
    client_secret: str,
    contact: str,
) -> tuple[int, int, int]:
    with SessionLocal() as db:
        tenant = Tenant(
            tenant_key=key,
            display_name=f"Tenant {key}",
            is_active=True,
        )
        db.add(tenant)
        db.flush()
        market = Market(
            tenant_id=tenant.id,
            tenant_assignment_source="test",
            tenant_assignment_version="r15",
            code=f"{key[:5].upper()}CH",
            name=f"Market {key}",
            country_code="CH",
            is_active=True,
        )
        db.add(market)
        db.flush()
        team = Team(
            tenant_id=tenant.id,
            tenant_assignment_source="test",
            tenant_assignment_version="r15",
            name=f"Support {key}",
            team_type="support",
            market_id=market.id,
            is_active=True,
        )
        db.add(team)
        db.flush()
        actor = User(
            tenant_id=tenant.id,
            tenant_assignment_source="test",
            tenant_assignment_version="r15",
            username=f"admin-{key}",
            display_name=f"Admin {key}",
            email=f"admin-{key}@example.test",
            password_hash=hash_password("pass123"),
            role=UserRole.admin,
            team_id=team.id,
            is_active=True,
        )
        customer = Customer(
            tenant_id=tenant.id,
            tenant_assignment_source="test",
            tenant_assignment_version="r15",
            name=f"Customer {key}",
            phone=contact,
            phone_normalized=contact,
        )
        db.add_all([actor, customer])
        db.flush()
        ticket = Ticket(
            tenant_id=tenant.id,
            tenant_assignment_source="test",
            tenant_assignment_version="r15",
            ticket_no=f"TENANT-{key.upper()}-001",
            title=f"Private ticket {key}",
            description=f"Private history for {key}",
            source=TicketSource.manual,
            source_channel=SourceChannel.whatsapp,
            priority=TicketPriority.high,
            status=TicketStatus.in_progress,
            customer_id=customer.id,
            team_id=team.id,
            assignee_id=actor.id,
            country_code="CH",
            preferred_reply_contact=contact,
            source_chat_id=contact,
        )
        integration = IntegrationClient(
            name=f"Client {key}",
            key_id=client_key,
            secret_hash=hash_secret(client_secret),
            scopes_csv="profile.read,task.write",
            rate_limit_per_minute=1000,
            is_active=True,
        )
        db.add_all([ticket, integration])
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
        return int(tenant.id), int(customer.id), int(ticket.id)


def _headers(key: str, secret: str, idem: str | None = None) -> dict[str, str]:
    headers = {
        "X-Client-Key-Id": key,
        "X-Client-Key": secret,
    }
    if idem:
        headers["Idempotency-Key"] = idem
    return headers


def test_profile_read_is_tenant_scoped_for_same_contact():
    contact = "+41795550123"
    tenant_a, customer_a, ticket_a = _tenant(
        key="alpha",
        client_key="client-alpha",
        client_secret="secret-alpha",
        contact=contact,
    )
    tenant_b, customer_b, ticket_b = _tenant(
        key="bravo",
        client_key="client-bravo",
        client_secret="secret-bravo",
        contact=contact,
    )

    alpha = client.get(
        f"/api/v1/integration/profile/{contact}",
        headers=_headers("client-alpha", "secret-alpha"),
    )
    bravo = client.get(
        f"/api/v1/integration/profile/{contact}",
        headers=_headers("client-bravo", "secret-bravo"),
    )

    assert alpha.status_code == 200, alpha.text
    assert bravo.status_code == 200, bravo.text
    assert alpha.json()["customer"]["id"] == customer_a
    assert bravo.json()["customer"]["id"] == customer_b
    assert [row["id"] for row in alpha.json()["dispute_history"]] == [ticket_a]
    assert [row["id"] for row in bravo.json()["dispute_history"]] == [ticket_b]
    assert ticket_b not in {row["id"] for row in alpha.json()["dispute_history"]}
    assert ticket_a not in {row["id"] for row in bravo.json()["dispute_history"]}

    with SessionLocal() as db:
        logs = db.execute(
            select(IntegrationRequestLog).order_by(IntegrationRequestLog.id.asc())
        ).scalars().all()
        assert len(logs) == 2
        for row in logs:
            stored = json.loads(row.response_json or "{}")
            assert "customer" not in stored
            assert "active_tasks" not in stored
            assert "dispute_history" not in stored
        envelopes = db.execute(
            select(IntegrationRequestLogEnvelope).order_by(
                IntegrationRequestLogEnvelope.log_id.asc()
            )
        ).scalars().all()
        assert {row.tenant_id for row in envelopes} == {tenant_a, tenant_b}


def test_task_write_and_duplicate_detection_are_tenant_scoped():
    contact = "+41795550999"
    tenant_a, _customer_a, original_a = _tenant(
        key="alpha",
        client_key="client-alpha",
        client_secret="secret-alpha",
        contact=contact,
    )
    tenant_b, _customer_b, original_b = _tenant(
        key="bravo",
        client_key="client-bravo",
        client_secret="secret-bravo",
        contact=contact,
    )
    payload = {
        "contact_id": contact,
        "channel": "whatsapp",
        "summary": "New integration request",
        "description": "Same contact and tracking must remain Tenant local.",
        "tracking_number": "CH-SAME-TRACKING",
        "priority": "high",
        "country_code": "CH",
    }

    alpha = client.post(
        "/api/v1/integration/task",
        json=payload,
        headers=_headers("client-alpha", "secret-alpha", "same-key"),
    )
    bravo = client.post(
        "/api/v1/integration/task",
        json=payload,
        headers=_headers("client-bravo", "secret-bravo", "same-key"),
    )

    assert alpha.status_code == 200, alpha.text
    assert bravo.status_code == 200, bravo.text
    # Each client may reuse its own pre-existing open Ticket, but it must never
    # receive the other Tenant's case reference.
    assert alpha.json()["case_ref"] != bravo.json()["case_ref"]

    with SessionLocal() as db:
        alpha_ticket = db.execute(
            select(Ticket).where(Ticket.ticket_no == alpha.json()["case_ref"])
        ).scalar_one()
        bravo_ticket = db.execute(
            select(Ticket).where(Ticket.ticket_no == bravo.json()["case_ref"])
        ).scalar_one()
        assert alpha_ticket.tenant_id == tenant_a
        assert bravo_ticket.tenant_id == tenant_b
        assert alpha_ticket.id != original_b
        assert bravo_ticket.id != original_a


def test_tenant_client_cannot_override_target_tenant():
    contact = "+41795550777"
    _tenant(
        key="alpha",
        client_key="client-alpha",
        client_secret="secret-alpha",
        contact=contact,
    )
    _tenant(
        key="bravo",
        client_key="client-bravo",
        client_secret="secret-bravo",
        contact=contact,
    )

    profile = client.get(
        f"/api/v1/integration/profile/{contact}?tenant_key=bravo",
        headers=_headers("client-alpha", "secret-alpha"),
    )
    task = client.post(
        "/api/v1/integration/task",
        json={
            "contact_id": contact,
            "summary": "Attempted target override",
            "tenant_key": "bravo",
        },
        headers=_headers("client-alpha", "secret-alpha", "override-key"),
    )

    assert profile.status_code == 403, profile.text
    assert task.status_code == 403, task.text
