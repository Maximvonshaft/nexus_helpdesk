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
from app.services.operator_queue import OperatorQueueError, project_operator_queue, replay_operator_task, transition_operator_task  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "operator_queue_audit.db"
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
        payload_json=json.dumps({"type": "message"}),
        status="pending",
        replay_count=0,
        last_error="provider error",
    )
    db.add(row)
    db.flush()
    return row


def test_assign_resolve_drop_and_replay_write_admin_audit(db_session):
    admin = make_user(db_session)
    row = OperatorTask(source_type="webchat", source_id="wc-1", task_type="handoff", status="pending", priority=40)
    db_session.add(row)
    event = make_unresolved(db_session)
    db_session.commit()
    project_operator_queue(db_session, actor_id=admin.id, note="project audit")
    openclaw_task = db_session.query(OperatorTask).filter_by(unresolved_event_id=event.id).one()

    transition_operator_task(db_session, task_id=row.id, action="assign", actor_id=admin.id, note="assign audit")
    transition_operator_task(db_session, task_id=row.id, action="resolve", actor_id=admin.id, note="resolve audit")
    replay_operator_task(db_session, task_id=openclaw_task.id, actor_id=admin.id, note="replay audit", replay_func=lambda db, *, row: True)

    event2 = make_unresolved(db_session)
    db_session.commit()
    project_operator_queue(db_session, actor_id=admin.id)
    dropped = db_session.query(OperatorTask).filter_by(unresolved_event_id=event2.id).one()
    transition_operator_task(db_session, task_id=dropped.id, action="drop", actor_id=admin.id, note="drop audit")

    actions = {row.action for row in db_session.query(AdminAuditLog).all()}
    assert "operator_queue.project" in actions
    assert "operator_queue.assign" in actions
    assert "operator_queue.resolve" in actions
    assert "operator_queue.drop" in actions
    assert "operator_queue.replayed" in actions


def test_replay_failed_writes_admin_audit(db_session):
    admin = make_user(db_session)
    make_unresolved(db_session)
    db_session.commit()
    project_operator_queue(db_session, actor_id=admin.id)
    task = db_session.query(OperatorTask).filter_by(source_type="openclaw").one()

    with pytest.raises(OperatorQueueError):
        replay_operator_task(db_session, task_id=task.id, actor_id=admin.id, note="failure audit", replay_func=lambda db, *, row: False)

    audit = db_session.query(AdminAuditLog).filter_by(action="operator_queue.replay_failed").one()
    assert "failure audit" in (audit.new_value_json or "")
