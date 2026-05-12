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
os.environ.setdefault("OPENCLAW_BRIDGE_ENABLED", "false")
os.environ.setdefault("WEBCHAT_FRONTLINE_AI_ENABLED", "true")
os.environ.setdefault("WEBCHAT_FORMAL_OUTBOUND_ENABLED", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.db import Base, get_db  # noqa: E402
from app.enums import JobStatus, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import BackgroundJob, Customer, Ticket, User  # noqa: E402
from app.services.background_jobs import WEBCHAT_AI_REPLY_JOB  # noqa: E402
from app.services.operator_queue import create_operator_task, transition_operator_task  # noqa: E402
from app.webchat_models import WebchatAITurn, WebchatConversation, WebchatEvent, WebchatMessage  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "event_isolation.db"
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
    row = User(username="admin", display_name="Admin", email="admin@example.test", password_hash="x", role=UserRole.admin, is_active=True)
    db.add(row)
    db.flush()
    return row


def make_webchat_conversation(db, *, public_id: str, visitor_token: str) -> WebchatConversation:
    customer = Customer(name="Isolation Visitor", email="visitor@example.invalid")
    db.add(customer)
    db.flush()
    ticket = Ticket(
        ticket_no="WC-ISOLATION-1",
        title="WebChat isolation ticket",
        description="Event write isolation fixture",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        status=TicketStatus.in_progress,
        preferred_reply_channel=SourceChannel.email.value,
        preferred_reply_contact="visitor@example.invalid",
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


def test_operator_assign_survives_safe_event_writer_failure(db_session, monkeypatch, caplog):
    admin = make_admin(db_session)
    task, _ = create_operator_task(
        db_session,
        source_type="webchat",
        source_id="wc_public",
        ticket_id=123,
        webchat_conversation_id=456,
        task_type="handoff",
        reason_code="customer_requested_human",
    )
    db_session.commit()

    def boom(*args, **kwargs):
        raise RuntimeError("raw secret token should not leak")

    monkeypatch.setattr("app.services.operator_queue.safe_write_webchat_event", boom)
    caplog.set_level("WARNING", logger="nexusdesk")

    transitioned = transition_operator_task(db_session, task_id=task.id, action="assign", actor_id=admin.id)

    assert transitioned.status == "assigned"
    assert transitioned.assignee_id == admin.id
    rendered_logs = "\n".join(record.getMessage() for record in caplog.records)
    assert "operator_queue_webchat_event_write_failed" in rendered_logs
    assert "raw secret token should not leak" not in rendered_logs


def test_operator_resolve_and_drop_main_state_still_success_without_event_dependency(db_session):
    admin = make_admin(db_session)
    resolve_task, _ = create_operator_task(db_session, source_type="openclaw", source_id="u1", unresolved_event_id=1, task_type="bridge_unresolved")
    drop_task, _ = create_operator_task(db_session, source_type="openclaw", source_id="u2", unresolved_event_id=2, task_type="bridge_unresolved")
    db_session.commit()

    resolved = transition_operator_task(db_session, task_id=resolve_task.id, action="resolve", actor_id=admin.id)
    dropped = transition_operator_task(db_session, task_id=drop_task.id, action="drop", actor_id=admin.id)

    assert resolved.status == "resolved"
    assert dropped.status == "dropped"
    assert resolved.resolved_at is not None
    assert dropped.resolved_at is not None


def test_frontline_ai_visitor_message_survives_webchat_event_flush_failure(api_context, monkeypatch, caplog):
    db_session, client = api_context
    public_id = "wc_ai_isolation"
    visitor_token = "visitor-token-for-event-isolation-0001"
    conversation = make_webchat_conversation(db_session, public_id=public_id, visitor_token=visitor_token)
    real_flush = db_session.flush

    def flaky_flush(*args, **kwargs):
        if any(isinstance(obj, WebchatEvent) for obj in db_session.new):
            raise RuntimeError("raw secret token visitor@example.invalid should not leak")
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
    assert payload.get("frontline_ai_enabled") is not False
    assert "ai_status" in payload
    assert "ai_pending" in payload
    assert "raw secret" not in response.text

    visitor_message = db_session.query(WebchatMessage).filter(WebchatMessage.conversation_id == conversation.id, WebchatMessage.direction == "visitor").one()
    db_session.refresh(conversation)
    turns = db_session.query(WebchatAITurn).filter(WebchatAITurn.conversation_id == conversation.id).all()
    jobs = db_session.query(BackgroundJob).filter(BackgroundJob.job_type == WEBCHAT_AI_REPLY_JOB, BackgroundJob.status.in_([JobStatus.pending, JobStatus.processing])).all()
    assert visitor_message.body.startswith("Please help with tracking number")
    assert len(turns) == 1
    assert len(jobs) == 1
    assert conversation.active_ai_turn_id == turns[0].id
    assert conversation.active_ai_status in {"queued", "processing", "bridge_calling"}

    rendered_logs = "\n".join(record.getMessage() for record in caplog.records)
    assert "raw secret token" not in rendered_logs
    assert "visitor@example.invalid" not in rendered_logs
    assert "SECRET_RAW_PII" not in rendered_logs
