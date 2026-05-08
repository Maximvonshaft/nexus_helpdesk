from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/operator_queue_concurrency_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.db import Base  # noqa: E402
from app.enums import ConversationState, SourceChannel, TicketPriority, TicketSource, UserRole  # noqa: E402
from app.models import OpenClawUnresolvedEvent, Ticket, User  # noqa: E402
from app.operator_models import OperatorTask  # noqa: E402
from app.services import operator_queue  # noqa: E402
from app.services.operator_queue import create_operator_task, project_operator_queue  # noqa: E402
from app.webchat_models import WebchatConversation  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "operator_queue_concurrency.db"
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


def make_admin(db):
    row = User(username="admin", display_name="admin", email="admin@example.test", password_hash="x", role=UserRole.admin, is_active=True)
    db.add(row)
    db.flush()
    return row


def make_unresolved(db):
    row = OpenClawUnresolvedEvent(
        source="default",
        session_key="session-key",
        event_type="message",
        recipient="recipient",
        source_chat_id="chat",
        preferred_reply_contact="contact",
        payload_json=json.dumps({"type": "message"}),
        status="pending",
        replay_count=0,
        last_error="provider error",
    )
    db.add(row)
    db.flush()
    return row


def make_ticket(db):
    row = Ticket(
        ticket_no="T-RACE-WEBCHAT",
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


def make_conversation(db, ticket: Ticket) -> WebchatConversation:
    row = WebchatConversation(
        public_id="wc-race",
        visitor_token_hash="hash",
        tenant_key="default",
        channel_key="default",
        ticket_id=ticket.id,
    )
    db.add(row)
    db.flush()
    return row


def test_integrity_error_for_same_unresolved_event_returns_existing_not_500(db_session, monkeypatch):
    event = make_unresolved(db_session)
    existing = OperatorTask(
        source_type="openclaw",
        source_id=str(event.id),
        unresolved_event_id=event.id,
        task_type="bridge_unresolved",
        status="pending",
        priority=50,
    )
    db_session.add(existing)
    db_session.flush()

    calls = {"count": 0}

    def fake_find_existing(*args, **kwargs):
        calls["count"] += 1
        return None if calls["count"] == 1 else existing

    monkeypatch.setattr(operator_queue, "_find_existing_active_task", fake_find_existing)

    def raise_unique_violation():
        raise IntegrityError("insert into operator_tasks", {}, Exception("unique active task violation"))

    monkeypatch.setattr(db_session, "flush", raise_unique_violation)

    row, created = create_operator_task(
        db_session,
        source_type="openclaw",
        source_id=str(event.id),
        unresolved_event_id=event.id,
        task_type="bridge_unresolved",
    )

    assert created is False
    assert row.id == existing.id


def test_integrity_error_for_same_webchat_handoff_returns_existing_not_500(db_session, monkeypatch):
    ticket = make_ticket(db_session)
    conversation = make_conversation(db_session, ticket)
    existing = OperatorTask(
        source_type="webchat",
        source_id=conversation.public_id,
        ticket_id=ticket.id,
        webchat_conversation_id=conversation.id,
        task_type="handoff",
        status="pending",
        priority=40,
    )
    db_session.add(existing)
    db_session.flush()

    calls = {"count": 0}

    def fake_find_existing(*args, **kwargs):
        calls["count"] += 1
        return None if calls["count"] == 1 else existing

    monkeypatch.setattr(operator_queue, "_find_existing_active_task", fake_find_existing)

    def raise_unique_violation():
        raise IntegrityError("insert into operator_tasks", {}, Exception("unique active task violation"))

    monkeypatch.setattr(db_session, "flush", raise_unique_violation)

    row, created = create_operator_task(
        db_session,
        source_type="webchat",
        source_id=conversation.public_id,
        ticket_id=ticket.id,
        webchat_conversation_id=conversation.id,
        task_type="handoff",
    )

    assert created is False
    assert row.id == existing.id


def test_project_operator_queue_skipped_existing_counts_are_correct(db_session):
    admin = make_admin(db_session)
    event = make_unresolved(db_session)
    ticket = make_ticket(db_session)
    conversation = make_conversation(db_session, ticket)
    db_session.add(OperatorTask(
        source_type="openclaw",
        source_id=str(event.id),
        unresolved_event_id=event.id,
        task_type="bridge_unresolved",
        status="pending",
        priority=50,
    ))
    db_session.add(OperatorTask(
        source_type="webchat",
        source_id=conversation.public_id,
        ticket_id=ticket.id,
        webchat_conversation_id=conversation.id,
        task_type="handoff",
        status="pending",
        priority=40,
    ))
    db_session.flush()

    summary = project_operator_queue(db_session, actor_id=admin.id)

    assert summary["created_total"] == 0
    assert summary["projected_openclaw_unresolved"] == 0
    assert summary["projected_webchat_handoff"] == 0
    assert summary["skipped_existing"] == 2
    assert db_session.query(OperatorTask).count() == 2


def test_get_operator_queue_projection_does_not_duplicate_active_tasks(db_session):
    admin = make_admin(db_session)
    make_unresolved(db_session)
    ticket = make_ticket(db_session)
    make_conversation(db_session, ticket)

    first = project_operator_queue(db_session, actor_id=admin.id)
    second = project_operator_queue(db_session, actor_id=admin.id)

    assert first["created_total"] == 2
    assert second["created_total"] == 0
    assert second["skipped_existing"] == 2
    assert db_session.query(OperatorTask).count() == 2
