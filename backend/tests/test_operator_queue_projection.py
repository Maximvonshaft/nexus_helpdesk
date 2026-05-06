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
from app.models import OpenClawUnresolvedEvent, Ticket, User  # noqa: E402
from app.operator_models import OperatorTask  # noqa: E402
from app.services.operator_queue import project_operator_queue, transition_operator_task  # noqa: E402
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


def make_unresolved(db, *, status="pending", payload=None):
    row = OpenClawUnresolvedEvent(
        source="default",
        session_key="sess-secret",
        event_type="message",
        recipient="+411234567",
        source_chat_id="+411234567",
        preferred_reply_contact="+411234567",
        payload_json=json.dumps(payload or {"type": "message", "sessionKey": "sess-secret"}, ensure_ascii=False),
        status=status,
        replay_count=0,
        last_error="Full provider error with sensitive context",
    )
    db.add(row)
    db.flush()
    return row


def test_pending_openclaw_event_projects_to_operator_task(db_session):
    admin = make_user(db_session)
    event = make_unresolved(db_session)
    db_session.commit()

    result = project_operator_queue(db_session, actor_id=admin.id, note="project openclaw")

    assert result["projected_openclaw_unresolved"] == 1
    task = db_session.query(OperatorTask).filter_by(unresolved_event_id=event.id).one()
    assert task.source_type == "openclaw"
    assert task.task_type == "bridge_unresolved"


def test_webchat_required_action_projects_to_operator_task(db_session):
    admin = make_user(db_session)
    ticket = make_ticket(db_session, required_action="manual_review")
    conversation = make_webchat(db_session, ticket)
    db_session.commit()

    result = project_operator_queue(db_session, actor_id=admin.id)

    assert result["projected_webchat_handoff"] == 1
    task = db_session.query(OperatorTask).filter_by(webchat_conversation_id=conversation.id).one()
    assert task.source_type == "webchat"
    assert task.task_type == "handoff"


def test_repeated_project_does_not_duplicate_active_tasks(db_session):
    admin = make_user(db_session)
    ticket = make_ticket(db_session)
    make_webchat(db_session, ticket)
    make_unresolved(db_session)
    db_session.commit()

    first = project_operator_queue(db_session, actor_id=admin.id)
    second = project_operator_queue(db_session, actor_id=admin.id)

    assert first["created_total"] == 2
    assert second["created_total"] == 0
    assert second["skipped_existing"] == 2
    assert db_session.query(OperatorTask).count() == 2


def test_webchat_source_closure_prevents_reprojection_after_resolve(db_session):
    admin = make_user(db_session)
    ticket = make_ticket(db_session)
    conversation = make_webchat(db_session, ticket)
    db_session.commit()

    project_operator_queue(db_session, actor_id=admin.id)
    task = db_session.query(OperatorTask).filter_by(webchat_conversation_id=conversation.id).one()
    transition_operator_task(db_session, task_id=task.id, action="resolve", actor_id=admin.id, note="handled")
    db_session.commit()

    result = project_operator_queue(db_session, actor_id=admin.id)

    db_session.refresh(ticket)
    assert ticket.required_action is None
    assert ticket.conversation_state != ConversationState.human_review_required
    assert result["created_total"] == 0
    assert db_session.query(OperatorTask).count() == 1


def test_openclaw_source_closure_prevents_reprojection_after_drop(db_session):
    admin = make_user(db_session)
    event = make_unresolved(db_session)
    db_session.commit()

    project_operator_queue(db_session, actor_id=admin.id)
    task = db_session.query(OperatorTask).filter_by(unresolved_event_id=event.id).one()
    transition_operator_task(db_session, task_id=task.id, action="drop", actor_id=admin.id, note="not actionable")
    db_session.commit()

    result = project_operator_queue(db_session, actor_id=admin.id)

    db_session.refresh(event)
    assert event.status == "dropped"
    assert result["created_total"] == 0
    assert db_session.query(OperatorTask).count() == 1
