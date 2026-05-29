from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/governance_release_queue_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.auth_service import create_access_token
from app.db import Base, SessionLocal, engine
from app.enums import UserRole
from app.main import app
from app.models import AIConfigResource, AdminAuditLog, User
from app.models_control_plane import GovernanceReleaseEvent, GovernanceReleaseRequest


@pytest.fixture(scope="module", autouse=True)
def ensure_schema():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        users = [
            User(id=9401, username="governance_admin", display_name="Governance Admin", password_hash="test", role=UserRole.admin, is_active=True),
            User(id=9402, username="governance_manager", display_name="Governance Manager", password_hash="test", role=UserRole.manager, is_active=True),
            User(id=9403, username="governance_agent", display_name="Governance Agent", password_hash="test", role=UserRole.agent, is_active=True),
            User(id=9404, username="governance_auditor", display_name="Governance Auditor", password_hash="test", role=UserRole.auditor, is_active=True),
        ]
        for user in users:
            existing = db.query(User).filter(User.id == user.id).first()
            if existing is None:
                db.add(user)
            else:
                existing.username = user.username
                existing.display_name = user.display_name
                existing.role = user.role
                existing.is_active = True
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def clean_rows():
    db = SessionLocal()
    try:
        db.query(GovernanceReleaseEvent).delete()
        db.query(GovernanceReleaseRequest).delete()
        db.query(AdminAuditLog).filter(AdminAuditLog.target_type == "governance_release").delete()
        db.query(AIConfigResource).filter(AIConfigResource.resource_key.like("pytest.governance.%")).delete()
        db.commit()
    finally:
        db.close()


def _headers(user_id: int = 9402) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def _source_resource() -> int:
    db = SessionLocal()
    try:
        row = AIConfigResource(
            resource_key="pytest.governance.release",
            config_type="sop",
            name="Governance release source",
            description="source row",
            scope_type="global",
            is_active=True,
            draft_summary="new release behavior",
            draft_content_json={"rule": "require approval before publish"},
            created_by=9402,
            updated_by=9402,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id
    finally:
        db.close()


def test_governance_release_queue_lifecycle_writes_events_and_admin_audit():
    client = TestClient(app)
    source_id = _source_resource()
    created = client.post(
        "/api/admin/governance-releases",
        headers=_headers(),
        json={
            "source_type": "ai_config",
            "source_id": source_id,
            "title": "Publish SOP guardrail",
            "summary": "Release the new published AI SOP after manager approval.",
            "release_type": "publish",
            "risk_level": "high",
            "impact_json": {"channels": ["webchat", "email"], "customers_affected": "policy scoped"},
            "diff_json": {"before": {"handoff": "manual"}, "after": {"handoff": "policy gated"}},
            "rollback_plan": "Rollback AI config to the previous published version and keep this request as evidence.",
        },
    )

    assert created.status_code == 200, created.text
    payload = created.json()
    release_id = payload["id"]
    assert payload["status"] == "pending_review"
    assert payload["source_type"] == "ai_config"
    assert payload["source_id"] == source_id
    assert [event["event_type"] for event in payload["events"]] == ["created", "submitted"]
    assert payload["events"][0]["request_id"]

    approved = client.post(f"/api/admin/governance-releases/{release_id}/approve", headers=_headers(), json={"note": "Diff reviewed."})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert approved.json()["approved_by"] == 9402

    published = client.post(f"/api/admin/governance-releases/{release_id}/publish", headers=_headers(), json={"note": "Published during ops window."})
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "published"

    rolled_back = client.post(f"/api/admin/governance-releases/{release_id}/rollback", headers=_headers(), json={"note": "Rollback drill evidence."})
    assert rolled_back.status_code == 200, rolled_back.text
    rolled_payload = rolled_back.json()
    assert rolled_payload["status"] == "rolled_back"
    assert [event["event_type"] for event in rolled_payload["events"]] == ["created", "submitted", "approve", "publish", "rollback"]

    invalid = client.post(f"/api/admin/governance-releases/{release_id}/publish", headers=_headers(), json={"note": "invalid"})
    assert invalid.status_code == 409
    assert "governance_release_invalid_transition" in invalid.json()["detail"]

    db = SessionLocal()
    try:
        audits = db.query(AdminAuditLog).filter(AdminAuditLog.target_type == "governance_release", AdminAuditLog.target_id == release_id).all()
        assert {row.action for row in audits} == {
            "governance_release.create",
            "governance_release.approve",
            "governance_release.publish",
            "governance_release.rollback",
        }
        events = db.query(GovernanceReleaseEvent).filter(GovernanceReleaseEvent.release_id == release_id).all()
        assert len(events) == 5
        assert all(event.request_id for event in events)
    finally:
        db.close()


def test_governance_release_queue_rbac_read_and_manage_are_distinct():
    client = TestClient(app)
    source_id = _source_resource()
    forbidden_create = client.post(
        "/api/admin/governance-releases",
        headers=_headers(9403),
        json={"source_type": "ai_config", "source_id": source_id, "title": "Agent try", "summary": "Agent cannot create this.", "risk_level": "low"},
    )
    assert forbidden_create.status_code == 403
    assert forbidden_create.json()["detail"] == "governance_release_manage_requires_capability"

    created = client.post(
        "/api/admin/governance-releases",
        headers=_headers(9402),
        json={"source_type": "ai_config", "source_id": source_id, "title": "Auditor visible", "summary": "Auditor can inspect queue.", "risk_level": "medium"},
    )
    assert created.status_code == 200, created.text

    auditor_list = client.get("/api/admin/governance-releases", headers=_headers(9404))
    assert auditor_list.status_code == 200, auditor_list.text
    assert auditor_list.json()["total"] == 1

    auditor_approve = client.post(f"/api/admin/governance-releases/{created.json()['id']}/approve", headers=_headers(9404), json={"note": "try"})
    assert auditor_approve.status_code == 403
    assert auditor_approve.json()["detail"] == "governance_release_manage_requires_capability"
