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
from app.enums import UserRole  # noqa: E402
from app.models import AdminAuditLog, OpenClawUnresolvedEvent, User  # noqa: E402
from app.operator_models import OperatorTask  # noqa: E402
from app.services.operator_queue import OperatorQueueError, project_operator_queue, replay_operator_task  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "operator_queue_replay.db"
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


def make_user(db):
    row = User(username="admin", display_name="admin", email="admin@example.test", password_hash="x", role=UserRole.admin, is_active=True)
    db.add(row)
    db.flush()
    return row


def make_unresolved(db):
    row = OpenClawUnresolvedEvent(
        source="default",
        session_key="sess-secret",
        event_type="message",
        recipient="+411234567",
        source_chat_id="+411234567",
        preferred_reply_contact="+411234567",
        payload_json=json.dumps({"type": "message", "sessionKey": "sess-secret"}),
        status="pending",
        replay_count=0,
        last_error="provider error with token=abc",
    )
    db.add(row)
    db.flush()
    return row


def test_replay_success_uses_row_object_and_marks_replayed(db_session):
    admin = make_user(db_session)
    event = make_unresolved(db_session)
    db_session.commit()
    project_operator_queue(db_session, actor_id=admin.id)
    task = db_session.query(OperatorTask).filter_by(unresolved_event_id=event.id).one()
    calls = []

    def fake_replay(db, *, row):
        calls.append(row.id)
        return True

    row, result = replay_operator_task(db_session, task_id=task.id, actor_id=admin.id, note="safe replay", replay_func=fake_replay)

    assert calls == [event.id]
    assert result == {"ok": True, "status": "replayed"}
    assert row.status == "replayed"
    assert event.status == "replayed"
    assert db_session.query(AdminAuditLog).filter_by(action="operator_queue.replayed").count() == 1


def test_replay_failure_is_409_and_not_fake_success(db_session):
    admin = make_user(db_session)
    event = make_unresolved(db_session)
    db_session.commit()
    project_operator_queue(db_session, actor_id=admin.id)
    task = db_session.query(OperatorTask).filter_by(unresolved_event_id=event.id).one()

    with pytest.raises(OperatorQueueError) as exc:
        replay_operator_task(db_session, task_id=task.id, actor_id=admin.id, replay_func=lambda db, *, row: False)

    assert exc.value.status_code == 409
    db_session.refresh(task)
    db_session.refresh(event)
    assert task.status == "replay_failed"
    assert event.status == "replay_failed"
    assert db_session.query(AdminAuditLog).filter_by(action="operator_queue.replay_failed").count() == 1


def test_replay_missing_unresolved_event_returns_404(db_session):
    admin = make_user(db_session)
    task = OperatorTask(source_type="openclaw", source_id="missing", unresolved_event_id=999, task_type="bridge_unresolved", status="pending", priority=50)
    db_session.add(task)
    db_session.commit()

    with pytest.raises(OperatorQueueError) as exc:
        replay_operator_task(db_session, task_id=task.id, actor_id=admin.id, replay_func=lambda db, *, row: True)

    assert exc.value.status_code == 404
    assert exc.value.code == "unresolved_event_missing"
