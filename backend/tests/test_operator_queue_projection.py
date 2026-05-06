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
    db_file = tmp_path / "operator_queue_projection.db"
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


def make_ticket(db):
    row = Ticket(
        ticket_no=f"T-{db.query(Ticket).count() + 1}",
        title="Need human review",
        description="Need human review",
        source=TicketSource.manual,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        conversation_state=ConversationState.human_review_required,
        required_action="manual_review",
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
        last_error="provider error with sensitive context",
    )
    db.add(row)
    db.flush()
    return row


def test_project_is_idempotent_for_openclaw_and_webchat(db_session):
    admin = make_user(db_session)
    ticket = make_ticket(db_session)
    conversation = make_webchat(db_session, ticket)
    event = make_unresolved(db_session)
    db_session.commit()

    first = project_operator_queue(db_session, actor_id=admin.id, note="first")
    second = project_operator_queue(db_session, actor_id=admin.id, note="second")

    assert first["created_total"] == 2
    assert second["created_total"] == 0
    assert second["skipped_existing"] == 2
    assert db_session.query(OperatorTask).filter_by(webchat_conversation_id=conversation.id).count() == 1
    assert db_session.query(OperatorTask).filter_by(unresolved_event_id=event.id).count() == 1


def test_source_closure_prevents_reprojection(db_session):
    admin = make_user(db_session)
    ticket = make_ticket(db_session)
    conversation = make_webchat(db_session, ticket)
    event = make_unresolved(db_session)
    db_session.commit()

    project_operator_queue(db_session, actor_id=admin.id)
    webchat_task = db_session.query(OperatorTask).filter_by(webchat_conversation_id=conversation.id).one()
    openclaw_task = db_session.query(OperatorTask).filter_by(unresolved_event_id=event.id).one()

    transition_operator_task(db_session, task_id=webchat_task.id, action="resolve", actor_id=admin.id, note="done")
    transition_operator_task(db_session, task_id=openclaw_task.id, action="drop", actor_id=admin.id, note="done")
    db_session.commit()

    again = project_operator_queue(db_session, actor_id=admin.id)
    db_session.refresh(ticket)
    db_session.refresh(event)

    assert again["created_total"] == 0
    assert ticket.required_action is None
    assert event.status == "dropped"
    assert db_session.query(OperatorTask).count() == 2
