from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_event_isolation_tests.db")
os.environ.setdefault("WEBCHAT_RATE_LIMIT_BACKEND", "memory")
os.environ.setdefault("WEBCHAT_ALLOW_NO_ORIGIN", "true")
os.environ.setdefault("EXTERNAL_CHANNEL_BRIDGE_ENABLED", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.db import Base, get_db  # noqa: E402
from app.enums import SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Customer, Ticket, User  # noqa: E402
from app.services.operator_queue import (  # noqa: E402
    OperatorQueueError,
    create_operator_task,
    transition_operator_task,
)
from app.webchat_models import WebchatAITurn, WebchatConversation, WebchatEvent, WebchatMessage  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "event_isolation.db"
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


@pytest.fixture()
def api_context(db_session):
    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    try:
        yield db_session, client
    finally:
        app.dependency_overrides.clear()


def make_admin(db):
    row = User(
        username="admin",
        display_name="Admin",
        email="admin@example.test",
        password_hash="x",
        role=UserRole.admin,
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def make_webchat_conversation(
    db,
    *,
    public_id: str,
    visitor_token: str,
) -> WebchatConversation:
    customer = Customer(
        name="Isolation Visitor",
        email="visitor@example.invalid",
    )
    db.add(customer)
    db.flush()
    ticket = Ticket(
        ticket_no=f"WC-{public_id}",
        title="WebChat isolation ticket",
        description="Event write isolation fixture",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        status=TicketStatus.in_progress,
    )
    db.add(ticket)
    db.flush()
    conversation = WebchatConversation(
        public_id=public_id,
        visitor_token_hash=hashlib.sha256(visitor_token.encode()).hexdigest(),
        tenant_key="default",
        channel_key="default",
        ticket_id=ticket.id,
        visitor_name="Isolation Visitor",
        visitor_email="visitor@example.invalid",
        status="open",
    )
    db.add(conversation)
    db.flush()
    db.commit()
    return conversation


def test_operator_handoff_projection_cannot_issue_source_command(db_session):
    admin = make_admin(db_session)
    task, _ = create_operator_task(
        db_session,
        source_type="webchat_handoff",
        source_id="91",
        source_version=2,
        projection_schema="nexus.operator-task.webchat-handoff.v1",
        ticket_id=123,
        webchat_conversation_id=456,
        task_type="handoff",
        reason_code="customer_requested_human",
    )
    db_session.commit()

    with pytest.raises(OperatorQueueError) as exc:
        transition_operator_task(
            db_session,
            task_id=task.id,
            action="assign",
            actor_id=admin.id,
        )

    assert exc.value.code == "operator_task_projection_command_forbidden"
    db_session.refresh(task)
    assert task.status == "pending"
    assert task.assignee_id is None


def test_projection_owned_admin_task_transitions_without_webchat_event_dependency(
    db_session,
):
    admin = make_admin(db_session)
    task, _ = create_operator_task(
        db_session,
        source_type="control_tower",
        source_id="runtime-recovery",
        task_type="control_tower_action",
    )
    db_session.commit()

    resolved = transition_operator_task(
        db_session,
        task_id=task.id,
        action="resolve",
        actor_id=admin.id,
    )

    assert resolved.status == "resolved"
    assert resolved.resolved_at is not None


def test_ai_turn_visitor_message_survives_webchat_event_flush_failure(
    api_context,
    monkeypatch,
    caplog,
):
    db_session, client = api_context
    public_id = "wc_ai_isolation"
    visitor_token = "visitor-token-for-event-isolation-0001"
    conversation = make_webchat_conversation(
        db_session,
        public_id=public_id,
        visitor_token=visitor_token,
    )
    real_flush = db_session.flush

    def flaky_flush(*args, **kwargs):
        if any(isinstance(obj, WebchatEvent) for obj in db_session.new):
            raise RuntimeError(
                "raw secret token visitor@example.invalid should not leak"
            )
        return real_flush(*args, **kwargs)

    monkeypatch.setattr(db_session, "flush", flaky_flush)
    caplog.set_level("WARNING", logger="nexusdesk")

    response = client.post(
        f"/api/webchat/conversations/{public_id}/messages",
        headers={"X-Webchat-Visitor-Token": visitor_token},
        json={
            "body": "Please help with tracking number TEST123. customer pii visitor@example.invalid SECRET_RAW_PII",
            "client_message_id": "client-message-1",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["message"]["direction"] == "visitor"
    assert payload["ai_status"] == "queued"
    assert payload["ai_pending"] is True
    assert "raw secret" not in response.text

    visitor_message = (
        db_session.query(WebchatMessage)
        .filter(
            WebchatMessage.conversation_id == conversation.id,
            WebchatMessage.direction == "visitor",
        )
        .one()
    )
    db_session.refresh(conversation)
    turn = (
        db_session.query(WebchatAITurn)
        .filter(WebchatAITurn.conversation_id == conversation.id)
        .one()
    )
    assert visitor_message.body.startswith("Please help with tracking number")
    assert turn.status == "queued"
    assert conversation.active_ai_turn_id == turn.id
    assert conversation.active_ai_status == "queued"

    rendered_logs = "\n".join(record.getMessage() for record in caplog.records)
    assert "webchat_event_write_failed_best_effort" in rendered_logs
    assert "raw secret token" not in rendered_logs
    assert "visitor@example.invalid" not in rendered_logs
    assert "SECRET_RAW_PII" not in rendered_logs
