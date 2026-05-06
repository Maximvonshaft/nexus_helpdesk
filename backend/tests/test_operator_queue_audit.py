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
from app.enums import ConversationState, SourceChannel, TicketPriority, TicketSource, UserRole  # noqa: E402
from app.models import AdminAuditLog, OpenClawUnresolvedEvent, Ticket, User  # noqa: E402
from app.operator_models import OperatorTask  # noqa: E402
from app.services.operator_queue import OperatorQueueError, project_operator_queue, replay_operator_task, transition_operator_task  # noqa: E402
from app.webchat_models import WebchatConversation  # noqa: E402


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


def make_ticket(db, *, required_action="manual_review", state=ConversationState.human_review_required):
    row = Ticket(
        ticket_no=f"T-{db.query(Ticket).count() + 1}",
        title="Need human review",
        description="Need human review",
        source=TicketSource.manual,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        conversation_state=state,
        required_action=required_action,
    )
    db.add(row)
    db.flush()
    return row


def make_webchat(db, ticket):
    row = WebchatConversation(
        public_id=f"wc-{ticket.id}",
        visitor_token_hash="hash",
        tenant_key="default",
        channel_key="default",
        ticket_id=ticket.id,
        visitor_name="Visitor",
        visitor_email="visitor@example.test",
        visitor_phone="+411234567",
        origin="https://example.test",
    )
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


def _actions(db):
    return [row.action for row in db.query(AdminAuditLog).order_by(AdminAuditLog.id.asc()).all()]


def test_project_assign_resolve_drop_and_replay_write_admin_audit(db_session):
    admin = make_user(db_session)
    ticket = make_ticket(db_session)
    make_webchat(db_session, ticket)
    event = make_unresolved(db_session)
    db_session.commit()

    project_operator_queue(db_session, actor_id=admin.id, note="project audit")
    webchat_task = db_session.query(OperatorTask).filter_by(source_type="webchat").one()
    openclaw_task = db_session.query(OperatorTask).filter_by(source_type="openclaw", unresolved_event_id=event.id).one()

    transition_operator_task(db_session, task_id=webchat_task.id, action="assign", actor_id=admin.id, note="assign audit")
    transition_operator_task(db_session, task_id=webchat_task.id, action="resolve", actor_id=admin.id, note="resolve audit")
    transition_operator_task(db_session, task_id=openclaw_task.id, action="drop", actor_id=admin.id, note="drop audit")

    event2 = make_unresolved(db_session, payload={"type": "message", "sessionKey": "another"})
    db_session.commit()
    project_operator_queue(db_session, actor_id=admin.id, note="project replay")
    replay_task = db_session.query(OperatorTask).filter_by(unresolved_event_id=event2.id).one()
    replay_operator_task(db_session, task_id=replay_task.id, actor_id=admin.id, note="replay audit", replay_func=lambda db, *, row: True)

    actions = _actions(db_session)
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
    assert "replay_failed" in (audit.new_value_json or "")
