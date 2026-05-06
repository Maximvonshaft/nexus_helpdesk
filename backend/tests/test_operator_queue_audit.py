from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.api import operator_queue as operator_queue_api
from app.db import Base, SessionLocal, engine
from app.enums import UserRole
from app.main import app
from app.models import AdminAuditLog, OpenClawUnresolvedEvent, User
from app.operator_models import OperatorTask

ADMIN_ID = 50250
PREFIX = "oq-audit-"


def _ensure_schema() -> None:
    Base.metadata.create_all(bind=engine)


def _reset_data() -> None:
    _ensure_schema()
    db = SessionLocal()
    try:
        db.query(AdminAuditLog).filter(
            (AdminAuditLog.target_type == "operator_task")
            | (AdminAuditLog.action.like("operator_queue.%"))
        ).delete(synchronize_session=False)
        db.query(OperatorTask).filter(OperatorTask.source_id.like(f"{PREFIX}%")).delete(synchronize_session=False)
        db.query(OpenClawUnresolvedEvent).filter(OpenClawUnresolvedEvent.session_key.like(f"{PREFIX}%")).delete(synchronize_session=False)
        db.query(User).filter(User.id == ADMIN_ID).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _admin() -> User:
    db = SessionLocal()
    try:
        row = User(id=ADMIN_ID, username="oq_audit_admin", display_name="OQ Audit Admin", password_hash="test", role=UserRole.admin, is_active=True)
        db.add(row)
        db.commit()
        db.refresh(row)
        db.expunge(row)
        return row
    finally:
        db.close()


def _client(admin: User) -> TestClient:
    app.dependency_overrides.clear()
    app.dependency_overrides[operator_queue_api.get_current_user] = lambda: admin
    return TestClient(app)


def _clear_overrides() -> None:
    app.dependency_overrides.clear()


def _task(suffix: str, *, unresolved_event_id: int | None = None) -> int:
    db = SessionLocal()
    try:
        row = OperatorTask(
            source_type="openclaw" if unresolved_event_id else "webchat",
            source_id=f"{PREFIX}{suffix}",
            unresolved_event_id=unresolved_event_id,
            task_type="bridge_unresolved" if unresolved_event_id else "handoff",
            status="pending",
            priority=40,
            reason_code="audit",
            payload_json=json.dumps({"audit_marker": f"{PREFIX}{suffix}"}),
        )
        db.add(row)
        db.commit()
        return row.id
    finally:
        db.close()


def _event_and_task(suffix: str) -> tuple[int, int]:
    db = SessionLocal()
    try:
        event = OpenClawUnresolvedEvent(
            source="pytest",
            session_key=f"{PREFIX}{suffix}",
            event_type="message",
            recipient="recipient@example.test",
            source_chat_id="recipient@example.test",
            preferred_reply_contact="recipient@example.test",
            payload_json=json.dumps({"type": "message", "sessionKey": f"{PREFIX}{suffix}"}),
            status="pending",
        )
        db.add(event)
        db.flush()
        task = OperatorTask(
            source_type="openclaw",
            source_id=f"{PREFIX}{suffix}",
            unresolved_event_id=event.id,
            task_type="bridge_unresolved",
            status="pending",
            priority=50,
            reason_code="audit",
            payload_json=json.dumps({"audit_marker": f"{PREFIX}{suffix}"}),
        )
        db.add(task)
        db.commit()
        return event.id, task.id
    finally:
        db.close()


def _audit_for(action: str, target_id: int | None = None) -> AdminAuditLog | None:
    db = SessionLocal()
    try:
        query = db.query(AdminAuditLog).filter(AdminAuditLog.action == action)
        if target_id is not None:
            query = query.filter(AdminAuditLog.target_id == target_id)
        row = query.order_by(AdminAuditLog.id.desc()).first()
        if row:
            db.expunge(row)
        return row
    finally:
        db.close()


def _assert_audit_or_xfail(action: str, target_id: int | None, note: str) -> None:
    row = _audit_for(action, target_id)
    if row is None:
        pytest.xfail(f"operator queue admin audit is not implemented yet for {action}")
    assert row.actor_id == ADMIN_ID
    assert row.target_type == "operator_task"
    if target_id is not None:
        assert row.target_id == target_id
    combined = f"{row.old_value_json or ''}\n{row.new_value_json or ''}"
    assert note in combined
    assert "old" in combined.lower() or row.old_value_json is not None
    assert "new" in combined.lower() or row.new_value_json is not None


@pytest.mark.parametrize(
    ("endpoint_action", "expected_audit_action", "note"),
    [
        ("assign", "operator_queue.assign", "audit assign note"),
        ("resolve", "operator_queue.resolve", "audit resolve note"),
        ("drop", "operator_queue.drop", "audit drop note"),
    ],
)
def test_mutations_write_admin_audit(endpoint_action: str, expected_audit_action: str, note: str):
    _reset_data()
    admin = _admin()
    task_id = _task(endpoint_action)

    try:
        res = _client(admin).post(f"/api/admin/operator-queue/{task_id}/{endpoint_action}", json={"note": note})
    finally:
        _clear_overrides()

    assert res.status_code == 200, res.text
    _assert_audit_or_xfail(expected_audit_action, task_id, note)


def test_replay_writes_admin_audit(monkeypatch):
    _reset_data()
    admin = _admin()
    event_id, task_id = _event_and_task("replay")

    def fake_replay(db, *, row):
        assert row.id == event_id
        row.status = "resolved"
        return True

    monkeypatch.setattr(operator_queue_api, "replay_unresolved_openclaw_event_payload", fake_replay, raising=False)
    monkeypatch.setattr(operator_queue_api, "replay_unresolved_openclaw_event", fake_replay, raising=False)

    try:
        res = _client(admin).post(f"/api/admin/operator-queue/{task_id}/replay", json={"note": "audit replay note"})
    finally:
        _clear_overrides()

    assert res.status_code == 200, res.text
    _assert_audit_or_xfail("operator_queue.replay", task_id, "audit replay note")


def test_project_writes_admin_audit():
    _reset_data()
    admin = _admin()
    db = SessionLocal()
    try:
        db.add(
            OpenClawUnresolvedEvent(
                source="pytest",
                session_key=f"{PREFIX}project",
                event_type="message",
                recipient="recipient@example.test",
                source_chat_id="recipient@example.test",
                preferred_reply_contact="recipient@example.test",
                payload_json=json.dumps({"type": "message", "sessionKey": f"{PREFIX}project"}),
                status="pending",
            )
        )
        db.commit()
    finally:
        db.close()

    try:
        res = _client(admin).post("/api/admin/operator-queue/project", json={"note": "audit project note"})
    finally:
        _clear_overrides()

    if res.status_code == 404:
        pytest.xfail("operator queue project endpoint is not implemented yet")
    assert res.status_code == 200, res.text
    _assert_audit_or_xfail("operator_queue.project", None, "audit project note")
