from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Column, Integer, Table, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import canonical_osr_admin as osr_admin_api
from app.api.deps import get_current_user
from app.db import Base, get_db
from app.enums import UserRole
from app.models_osr import CaseContextRecord, RuntimeDecisionAuditRecord

if "webchat_conversations" not in Base.metadata.tables:
    Table("webchat_conversations", Base.metadata, Column("id", Integer, primary_key=True))
if "tickets" not in Base.metadata.tables:
    Table("tickets", Base.metadata, Column("id", Integer, primary_key=True))

TENANT_A = {"X-Nexus-Tenant": "tenant-a"}
TENANT_B = {"X-Nexus-Tenant": "tenant-b"}


def _serialized(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


@pytest.fixture()
def api_context(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    Base.metadata.create_all(engine)
    current = {"user": SimpleNamespace(id=1, role=UserRole.admin)}

    def override_get_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    def override_current_user():
        return current["user"]

    monkeypatch.setattr(osr_admin_api._core, "ensure_can_manage_runtime", lambda user, db: None)
    app = FastAPI()
    app.include_router(osr_admin_api.router)
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_current_user
    try:
        yield TestClient(app), SessionLocal, current
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_admin_crud_and_provider_group_redaction_are_consistent(api_context):
    client, _SessionLocal, _current = api_context
    raw_destination = "120363999999999999@g.us"
    raw_fallback = "120363888888888888@g.us"
    create = client.post(
        "/api/admin/osr/whatsapp-routing-rules",
        json={
            "country_code": "ME",
            "issue_type": "signed_not_received",
            "destination_group_id": raw_destination,
            "fallback_group_id": raw_fallback,
            "message_template": "Contact test@example.test or +382 67123456",
        },
    )
    assert create.status_code == 201, create.text
    rule_id = create.json()["id"]
    assert create.json()["configuration_scope"] == "global"
    assert raw_destination not in _serialized(create.json())
    assert raw_fallback not in _serialized(create.json())
    assert "test@example.test" not in _serialized(create.json())
    assert "+382 67123456" not in _serialized(create.json())

    listed = client.get("/api/admin/osr/whatsapp-routing-rules")
    assert listed.status_code == 200
    assert listed.json()["configuration_scope"] == "global"
    assert raw_destination not in _serialized(listed.json())
    assert raw_fallback not in _serialized(listed.json())

    updated = client.patch(
        f"/api/admin/osr/whatsapp-routing-rules/{rule_id}",
        json={"enabled": False},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["enabled"] is False

    deleted = client.delete(f"/api/admin/osr/whatsapp-routing-rules/{rule_id}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is False
    assert deleted.json()["disabled"] is True


def test_runtime_decision_audit_is_tenant_isolated_and_payload_free(api_context):
    client, SessionLocal, _current = api_context
    with SessionLocal() as db:
        context_a = CaseContextRecord(
            tenant_id="tenant-a",
            channel="webchat",
            country_code="ME",
            issue_type="signed_not_received",
            status="active",
            is_active=False,
            customer_claim_summary="Customer email test@example.test and +382 67123456",
        )
        db.add_all(
            [
                RuntimeDecisionAuditRecord(
                    tenant_id="tenant-a",
                    channel="webchat",
                    country_code="ME",
                    business_reply_type="handoff",
                    next_action="route_to_whatsapp",
                    risk_level="high",
                    allowed=True,
                    violations_json=[],
                    warnings_json=[],
                    decision_json={"evidence_sources": [{"source_id": "secret-source-a", "summary": "safe"}]},
                    case_context_json={"email": "test@example.test", "token": "secret-token"},
                ),
                RuntimeDecisionAuditRecord(
                    tenant_id="tenant-b",
                    channel="email",
                    country_code="MK",
                    business_reply_type="ticket",
                    next_action="create_ticket",
                    risk_level="medium",
                    allowed=False,
                    violations_json=["missing_evidence"],
                    warnings_json=[],
                    decision_json={"evidence_sources": []},
                    case_context_json={"phone": "+38970000000"},
                ),
                context_a,
            ]
        )
        db.commit()
        context_id = context_a.id

    audit = client.get("/api/admin/osr/runtime-decision-audits", headers=TENANT_A)
    assert audit.status_code == 200, audit.text
    payload = audit.json()
    assert payload["tenant_id"] == "tenant-a"
    assert payload["total"] == 1
    item = payload["items"][0]
    assert item["next_action"] == "route_to_whatsapp"
    serialized = _serialized(payload)
    assert "secret-source-a" not in serialized
    assert "secret-token" not in serialized
    assert "test@example.test" not in serialized
    assert "tenant-b" not in serialized

    contexts = client.get("/api/admin/osr/case-contexts", headers=TENANT_A)
    assert contexts.status_code == 200, contexts.text
    assert contexts.json()["total"] == 1
    assert contexts.json()["items"][0]["id"] == context_id
    assert "test@example.test" not in _serialized(contexts.json())

    other = client.get("/api/admin/osr/runtime-decision-audits", headers=TENANT_B)
    assert other.status_code == 200
    assert other.json()["total"] == 1
    assert other.json()["items"][0]["next_action"] == "create_ticket"


def test_admin_router_requires_runtime_permission(api_context, monkeypatch):
    client, _SessionLocal, _current = api_context

    def deny(_user, _db):
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="forbidden")

    monkeypatch.setattr(osr_admin_api._core, "ensure_can_manage_runtime", deny)
    response = client.get("/api/admin/osr/whatsapp-routing-rules")
    assert response.status_code == 403
