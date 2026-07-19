from __future__ import annotations

import json
from pathlib import Path
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

    monkeypatch.setattr(osr_admin_api, "ensure_can_manage_runtime", lambda user, db: None)
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
    assert create.status_code == 201
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
        json={"is_active": False},
    )
    assert updated.status_code == 200
    assert updated.json()["is_active"] is False

    deleted = client.delete(f"/api/admin/osr/whatsapp-routing-rules/{rule_id}")
    assert deleted.status_code == 204


def test_runtime_decision_audit_is_tenant_isolated_and_payload_free(api_context):
    client, SessionLocal, _current = api_context
    with SessionLocal() as db:
        db.add_all(
            [
                RuntimeDecisionAuditRecord(
                    tenant_key="tenant-a",
                    request_id="request-a",
                    issue_type="signed_not_received",
                    decision="route_to_whatsapp",
                    safe_summary={"destination_configured": True},
                ),
                RuntimeDecisionAuditRecord(
                    tenant_key="tenant-b",
                    request_id="request-b",
                    issue_type="damaged_parcel",
                    decision="create_ticket",
                    safe_summary={"destination_configured": False},
                ),
                CaseContextRecord(
                    tenant_key="tenant-a",
                    case_key="case-a",
                    context_version=1,
                    safe_context={"status": "open"},
                ),
            ]
        )
        db.commit()

    audit = client.get("/api/admin/osr/runtime-decisions", headers=TENANT_A)
    assert audit.status_code == 200
    payload = audit.json()
    assert payload["configuration_scope"] == "tenant"
    serialized = _serialized(payload)
    assert "request-a" in serialized
    assert "request-b" not in serialized
    assert "tenant-a" not in serialized

    contexts = client.get("/api/admin/osr/case-contexts", headers=TENANT_A)
    assert contexts.status_code == 200
    assert contexts.json()["items"][0]["case_key"] == "case-a"


def test_admin_router_requires_runtime_permission(api_context, monkeypatch):
    client, _SessionLocal, _current = api_context

    def deny(_user, _db):
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="forbidden")

    monkeypatch.setattr(osr_admin_api, "ensure_can_manage_runtime", deny)
    response = client.get("/api/admin/osr/whatsapp-routing-rules")
    assert response.status_code == 403
