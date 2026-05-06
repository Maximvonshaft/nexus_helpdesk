from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.api import operator_queue as operator_queue_api
from app.db import Base, SessionLocal, engine
from app.enums import UserRole
from app.main import app
from app.models import OpenClawUnresolvedEvent, User
from app.operator_models import OperatorTask

ADMIN_ID = 50150
PREFIX = "oq-replay-"


def _ensure_schema() -> None:
    Base.metadata.create_all(bind=engine)


def _reset_data() -> None:
    _ensure_schema()
    db = SessionLocal()
    try:
        db.query(OperatorTask).filter(OperatorTask.source_id.like(f"{PREFIX}%")).delete(synchronize_session=False)
        db.query(OpenClawUnresolvedEvent).filter(OpenClawUnresolvedEvent.session_key.like(f"{PREFIX}%")).delete(synchronize_session=False)
        db.query(User).filter(User.id == ADMIN_ID).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def _admin() -> User:
    db = SessionLocal()
    try:
        row = User(id=ADMIN_ID, username="oq_replay_admin", display_name="OQ Replay Admin", password_hash="test", role=UserRole.admin, is_active=True)
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


def _event_and_task(suffix: str = "case") -> tuple[int, int]:
    db = SessionLocal()
    try:
        event = OpenClawUnresolvedEvent(
            source="pytest",
            session_key=f"{PREFIX}{suffix}",
            event_type="message",
            recipient="recipient@example.test",
            source_chat_id="recipient@example.test",
            preferred_reply_contact="recipient@example.test",
            payload_json=json.dumps({"type": "message", "sessionKey": f"{PREFIX}{suffix}", "body": "Replay me"}),
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
            reason_code="openclaw_unresolved",
            payload_json=json.dumps({"session_key": f"{PREFIX}{suffix}"}),
        )
        db.add(task)
        db.commit()
        return event.id, task.id
    finally:
        db.close()


def _load(event_id: int, task_id: int) -> tuple[OpenClawUnresolvedEvent, OperatorTask]:
    db = SessionLocal()
    try:
        event = db.query(OpenClawUnresolvedEvent).filter(OpenClawUnresolvedEvent.id == event_id).first()
        task = db.query(OperatorTask).filter(OperatorTask.id == task_id).first()
        assert event is not None and task is not None
        db.expunge(event)
        db.expunge(task)
        return event, task
    finally:
        db.close()


def test_replay_endpoint_passes_unresolved_row_object_not_id(monkeypatch):
    _reset_data()
    event_id, task_id = _event_and_task("signature")
    admin = _admin()
    called = {}

    def fake_replay(db, *, row):
        called["row"] = row
        row.status = "resolved"
        return True

    monkeypatch.setattr(operator_queue_api, "replay_unresolved_openclaw_event_payload", fake_replay, raising=False)
    monkeypatch.setattr(operator_queue_api, "replay_unresolved_openclaw_event", fake_replay, raising=False)

    try:
        res = _client(admin).post(f"/api/admin/operator-queue/{task_id}/replay", json={"note": "signature check"})
    finally:
        _clear_overrides()

    assert res.status_code == 200, res.text
    assert isinstance(called.get("row"), OpenClawUnresolvedEvent)
    assert called["row"].id == event_id
    assert "TypeError" not in res.text


def test_replay_success_closes_source_and_task_with_result(monkeypatch):
    _reset_data()
    event_id, task_id = _event_and_task("success")
    admin = _admin()

    def fake_replay(db, *, row):
        row.status = "resolved"
        row.last_error = None
        return True

    monkeypatch.setattr(operator_queue_api, "replay_unresolved_openclaw_event_payload", fake_replay, raising=False)
    monkeypatch.setattr(operator_queue_api, "replay_unresolved_openclaw_event", fake_replay, raising=False)

    try:
        res = _client(admin).post(f"/api/admin/operator-queue/{task_id}/replay", json={"note": "safe replay"})
    finally:
        _clear_overrides()

    assert res.status_code == 200, res.text
    payload = res.json()
    assert payload["status"] == "replayed"
    assert payload["replay_result"] is True
    event, task = _load(event_id, task_id)
    assert event.status in {"resolved", "replayed"}
    assert task.status == "replayed"
    assert task.resolved_at is not None


def test_replay_false_result_does_not_blindly_mark_task_replayed(monkeypatch):
    _reset_data()
    event_id, task_id = _event_and_task("false-result")
    admin = _admin()

    def fake_replay(db, *, row):
        row.status = "failed"
        row.last_error = "No unique ticket match for auto-link"
        return False

    monkeypatch.setattr(operator_queue_api, "replay_unresolved_openclaw_event_payload", fake_replay, raising=False)
    monkeypatch.setattr(operator_queue_api, "replay_unresolved_openclaw_event", fake_replay, raising=False)

    try:
        res = _client(admin).post(f"/api/admin/operator-queue/{task_id}/replay", json={"note": "will fail"})
    finally:
        _clear_overrides()

    assert res.status_code in {400, 409, 502}, res.text
    assert "No unique ticket match" in res.text or "replay" in res.text.lower()
    event, task = _load(event_id, task_id)
    assert event.status == "failed"
    assert task.status != "replayed"
    assert "secret" not in res.text.lower()


def test_replay_exception_does_not_mark_task_replayed_and_exposes_safe_diagnostic(monkeypatch):
    _reset_data()
    event_id, task_id = _event_and_task("exception")
    admin = _admin()

    def fake_replay(db, *, row):
        row.status = "failed"
        row.last_error = "Bridge unavailable"
        raise RuntimeError("Bridge unavailable; token=should_not_leak")

    monkeypatch.setattr(operator_queue_api, "replay_unresolved_openclaw_event_payload", fake_replay, raising=False)
    monkeypatch.setattr(operator_queue_api, "replay_unresolved_openclaw_event", fake_replay, raising=False)

    try:
        res = _client(admin).post(f"/api/admin/operator-queue/{task_id}/replay", json={"note": "diagnostic"})
    finally:
        _clear_overrides()

    assert res.status_code in {400, 409, 502}, res.text
    assert "Bridge unavailable" in res.text or "replay" in res.text.lower()
    assert "should_not_leak" not in res.text
    _, task = _load(event_id, task_id)
    assert task.status != "replayed"
