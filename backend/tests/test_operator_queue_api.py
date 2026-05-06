from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/operator_queue_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.api.operator_queue import assign_operator_task, get_operator_queue, project_operator_queue_endpoint  # noqa: E402
from app.db import Base  # noqa: E402
from app.enums import ConversationState, SourceChannel, TicketPriority, TicketSource, UserRole  # noqa: E402
from app.models import AdminAuditLog, OpenClawUnresolvedEvent, Ticket, User  # noqa: E402
from app.operator_models import OperatorTask  # noqa: E402
from app.operator_schemas import OperatorTaskTransitionRequest  # noqa: E402
from app.services.operator_queue import create_operator_task  # noqa: E402
from app.webchat_models import WebchatConversation, WebchatEvent  # noqa: E402


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


def make_unresolved(db):
    row = OpenClawUnresolvedEvent(
        source="default",
        session_key="sess-secret",
        event_type="message",
        recipient="+411234567",
        source_chat_id="+411234567",
        preferred_reply_contact="+411234567",
        payload_json=json.dumps({"type": "message", "sessionKey": "sess-secret"}, ensure_ascii=False),
        status="pending",
        replay_count=0,
        last_error="Full provider error with sensitive context",
    )
    db.add(row)
    db.flush()
    return row


def test_get_queue_is_read_only(db_session):
    admin = make_user(db_session)
    ticket = make_ticket(db_session)
    make_webchat(db_session, ticket)
    make_unresolved(db_session)
    db_session.commit()

    before_tasks = db_session.query(OperatorTask).count()
    before_events = db_session.query(WebchatEvent).count()

    response = get_operator_queue(db=db_session, current_user=admin)

    assert response["items"] == []
    assert db_session.query(OperatorTask).count() == before_tasks
    assert db_session.query(WebchatEvent).count() == before_events


def test_post_project_explicitly_writes_projection(db_session):
    admin = make_user(db_session)
    ticket = make_ticket(db_session)
    make_webchat(db_session, ticket)
    make_unresolved(db_session)
    db_session.commit()

    response = project_operator_queue_endpoint(OperatorTaskTransitionRequest(note="project now"), db_session, admin)

    assert response["created_total"] == 2
    assert db_session.query(OperatorTask).count() == 2
    assert db_session.query(WebchatEvent).count() == 1
    audit_actions = [row.action for row in db_session.query(AdminAuditLog).order_by(AdminAuditLog.id.asc()).all()]
    assert "operator_queue.project" in audit_actions


def test_post_project_is_idempotent(db_session):
    admin = make_user(db_session)
    ticket = make_ticket(db_session)
    make_webchat(db_session, ticket)
    make_unresolved(db_session)
    db_session.commit()

    first = project_operator_queue_endpoint(OperatorTaskTransitionRequest(note="first"), db_session, admin)
    second = project_operator_queue_endpoint(OperatorTaskTransitionRequest(note="second"), db_session, admin)

    assert first["created_total"] == 2
    assert second["created_total"] == 0
    assert second["skipped_existing"] == 2
    assert db_session.query(OperatorTask).count() == 2


def test_runtime_manage_permission_required_for_get(db_session):
    agent = make_user(db_session, "agent", UserRole.agent)

    with pytest.raises(HTTPException) as exc:
        get_operator_queue(db=db_session, current_user=agent)

    assert exc.value.status_code == 403


def test_transition_request_extra_forbid():
    with pytest.raises(ValidationError):
        OperatorTaskTransitionRequest.model_validate({"note": "ok", "unexpected": "blocked"})


def test_note_is_preserved_on_assign_audit(db_session):
    admin = make_user(db_session)
    row, _ = create_operator_task(db_session, source_type="webchat", task_type="handoff", source_id="wc-1")
    db_session.commit()

    assign_operator_task(row.id, OperatorTaskTransitionRequest(note="assignment note"), db_session, admin)

    audit = db_session.query(AdminAuditLog).filter_by(action="operator_queue.assign").one()
    assert "assignment note" in (audit.new_value_json or "")
