from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/operator_queue_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.db import Base  # noqa: E402
from app.enums import SourceChannel, TicketPriority, TicketSource, UserRole  # noqa: E402
from app.models import AdminAuditLog, OpenClawUnresolvedEvent, Ticket, User  # noqa: E402
from app.operator_models import OperatorTask  # noqa: E402
from app.services.operator_queue import OperatorQueueError, project_operator_queue, replay_operator_task  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "operator_queue.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False}, future=True)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def make_user(db, username="admin", role=UserRole.admin):
    row = User(username=username, display_name=username, email=f"{username}@example.test", password_hash="x", role=role, is_active=True)
    db.add(row)
    db.flush()
    return row


def make_unresolved(db, *, payload=None):
    row = OpenClawUnresolvedEvent(
        source="default",
        session_key="sess-secret",
        event_type="message",
        recipient="+411234567",
        source_chat_id="+411234567",
        preferred_reply_contact="+411234567",
        payload_json=json.dumps(payload or {"type": "message", "sessionKey": "sess-secret"}, ensure_ascii=False),
        status="pending",
        replay_count=0,
        last_error="Full provider error with sensitive context",
    )
    db.add(row)
    db.flush()
    return row


def test_replay_service_receives_unresolved_event_row_object(db_session):
    admin = make_user(db_session)
    event = make_unresolved(db_session)
    db_session.commit()
    project_operator_queue(db_session, actor_id=admin.id)
    task = db_session.query(OperatorTask).filter_by(unresolved_event_id=event.id).one()
    calls = []

    def fake_replay(db, *, row):
        calls.append(row)
        assert row.id == event.id
        return True

    row, result = replay_operator_task(db_session, task_id=task.id, actor_id=admin.id, note="safe replay", replay_func=fake_replay)

    assert calls == [event]
    assert result == {"ok": True, "status": "replayed"}
    assert row.status == "replayed"
    assert event.status == "replayed"
    audit = db_session.query(AdminAuditLog).filter_by(action="operator_queue.replayed").one()
    assert "safe replay" in (audit.new_value_json or "")


def test_replay_failure_is_not_fake_success_and_returns_409(db_session):
    admin = make_user(db_session)
    event = make_unresolved(db_session)
    db_session.commit()
    project_operator_queue(db_session, actor_id=admin.id)
    task = db_session.query(OperatorTask).filter_by(unresolved_event_id=event.id).one()

    def fake_replay(db, *, row):
        row.last_error = "raw secret provider failure token=abc"
        return False

    with pytest.raises(OperatorQueueError) as exc:
        replay_operator_task(db_session, task_id=task.id, actor_id=admin.id, note="try replay", replay_func=fake_replay)

    assert exc.value.status_code == 409
    assert exc.value.detail == "replay failed"
    db_session.refresh(task)
    db_session.refresh(event)
    assert task.status == "replay_failed"
    assert event.status == "replay_failed"
    audit = db_session.query(AdminAuditLog).filter_by(action="operator_queue.replay_failed").one()
    assert "raw secret provider failure token=abc" not in (audit.new_value_json or "")


def test_replay_exception_is_sanitized_and_marks_failed(db_session):
    admin = make_user(db_session)
    event = make_unresolved(db_session)
    db_session.commit()
    project_operator_queue(db_session, actor_id=admin.id)
    task = db_session.query(OperatorTask).filter_by(unresolved_event_id=event.id).one()

    def fake_replay(db, *, row):
        raise RuntimeError("exploded with token=abc123 and private body")

    with pytest.raises(OperatorQueueError) as exc:
        replay_operator_task(db_session, task_id=task.id, actor_id=admin.id, replay_func=fake_replay)

    assert exc.value.status_code == 409
    assert exc.value.detail == "replay failed"
    db_session.refresh(task)
    db_session.refresh(event)
    assert task.status == "replay_failed"
    assert event.status == "replay_failed"
    assert event.last_error == "RuntimeError"


def test_unresolved_event_missing_returns_404(db_session):
    admin = make_user(db_session)
    task = OperatorTask(
        source_type="openclaw",
        source_id="missing",
        unresolved_event_id=999,
        task_type="bridge_unresolved",
        status="pending",
        priority=50,
    )
    db_session.add(task)
    db_session.commit()

    with pytest.raises(OperatorQueueError) as exc:
        replay_operator_task(db_session, task_id=task.id, actor_id=admin.id, replay_func=lambda db, *, row: True)

    assert exc.value.status_code == 404
    assert exc.value.code == "unresolved_event_missing"
