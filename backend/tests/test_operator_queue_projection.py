from __future__ import annotations

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
from app.models import Ticket, User  # noqa: E402
from app.operator_models import OperatorTask  # noqa: E402
from app.services.operator_queue import project_operator_queue  # noqa: E402
from app.utils.time import utc_now  # noqa: E402
from app.webchat_models import WebchatConversation, WebchatHandoffRequest  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "operator_queue_projection.db"
    engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        future=True,
        expire_on_commit=False,
    )
    Base.metadata.create_all(engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def make_user(db):
    row = User(
        username="admin",
        display_name="admin",
        email="admin@example.test",
        password_hash="x",
        role=UserRole.admin,
        is_active=True,
    )
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
        handoff_status="requested",
        ai_suspended=True,
    )
    db.add(row)
    db.flush()
    return row


def make_handoff(db, conversation, ticket):
    now = utc_now()
    row = WebchatHandoffRequest(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        source="ai_auto",
        trigger_type="handoff_required",
        status="requested",
        reason_code="human_review_required",
        recommended_agent_action="manual_review",
        requested_by_actor_type="system",
        requested_at=now,
        lock_version=1,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    conversation.current_handoff_request_id = row.id
    return row


def test_project_is_idempotent_for_handoff_request(db_session):
    admin = make_user(db_session)
    ticket = make_ticket(db_session)
    conversation = make_webchat(db_session, ticket)
    handoff = make_handoff(db_session, conversation, ticket)
    db_session.commit()

    first = project_operator_queue(db_session, actor_id=admin.id)
    second = project_operator_queue(db_session, actor_id=admin.id)

    assert first["projected_webchat_handoff"] == 1
    assert second["projected_webchat_handoff"] == 0
    task = (
        db_session.query(OperatorTask)
        .filter_by(webchat_conversation_id=conversation.id)
        .one()
    )
    assert task.source_type == "webchat_handoff"
    assert task.source_id == str(handoff.id)
    assert task.source_version == handoff.lock_version
    assert task.status == "pending"


def test_terminal_handoff_retires_projection_without_mutating_ticket(db_session):
    admin = make_user(db_session)
    ticket = make_ticket(db_session)
    conversation = make_webchat(db_session, ticket)
    handoff = make_handoff(db_session, conversation, ticket)
    db_session.commit()

    project_operator_queue(db_session, actor_id=admin.id)
    task = (
        db_session.query(OperatorTask)
        .filter_by(webchat_conversation_id=conversation.id)
        .one()
    )
    original_required_action = ticket.required_action
    original_conversation_state = ticket.conversation_state

    handoff.status = "closed"
    handoff.closed_at = utc_now()
    handoff.lock_version += 1
    handoff.updated_at = utc_now()
    db_session.commit()

    again = project_operator_queue(db_session, actor_id=admin.id)
    db_session.refresh(task)
    db_session.refresh(ticket)

    assert again["created_total"] == 0
    assert task.status == "resolved"
    assert task.source_version == handoff.lock_version
    assert task.resolved_at is not None
    assert ticket.required_action == original_required_action
    assert ticket.conversation_state == original_conversation_state


def test_accepted_handoff_projects_assignment_and_version(db_session):
    admin = make_user(db_session)
    ticket = make_ticket(db_session)
    conversation = make_webchat(db_session, ticket)
    handoff = make_handoff(db_session, conversation, ticket)
    handoff.status = "accepted"
    handoff.assigned_agent_id = admin.id
    handoff.accepted_by_user_id = admin.id
    handoff.lock_version = 4
    db_session.commit()

    project_operator_queue(db_session, actor_id=admin.id)
    task = db_session.query(OperatorTask).one()

    assert task.status == "assigned"
    assert task.assignee_id == admin.id
    assert task.source_version == 4
