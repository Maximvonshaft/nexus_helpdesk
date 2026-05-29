from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import models as _models  # noqa: E402,F401
from app import models_control_plane as _models_control_plane  # noqa: E402,F401
from app import voice_models as _voice_models  # noqa: E402,F401
from app import webchat_models as _webchat_models  # noqa: E402,F401
from app.auth_service import create_access_token  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.enums import ConversationState, ResolutionCategory, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AdminAuditLog, Customer, QAReview, QATrainingTask, Team, Ticket, TicketEvent, User  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "qa_training_loop.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False}, future=True)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture()
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _uid() -> str:
    return uuid.uuid4().hex[:10]


def _user(db_session, role: UserRole, username: str) -> User:
    row = User(
        username=f"{username}-{_uid()}",
        display_name=username.title(),
        email=f"{username}-{_uid()}@example.test",
        password_hash="test",
        role=role,
        is_active=True,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def _ticket(db_session, *, assignee: User | None = None, channel: SourceChannel = SourceChannel.web_chat) -> Ticket:
    team = Team(name=f"QA Team {_uid()}", team_type="support")
    customer = Customer(name="QA Customer", email="qa-customer@example.test", phone="+15550123456")
    db_session.add_all([team, customer])
    db_session.flush()
    row = Ticket(
        ticket_no=f"QA-{_uid()}",
        title="Customer could not get address update answer",
        description="Template QA sample",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=channel,
        priority=TicketPriority.urgent,
        status=TicketStatus.in_progress,
        resolution_category=ResolutionCategory.none,
        conversation_state=ConversationState.human_review_required,
        team_id=team.id,
        assignee_id=assignee.id if assignee else None,
        ai_confidence=0.42,
        missing_fields="order id, identity proof",
        preferred_reply_channel=channel.value,
        preferred_reply_contact="qa-customer@example.test",
        issue_summary="Address update failed",
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_qa_queue_uses_real_ticket_signals_and_read_rbac(client: TestClient, db_session):
    auditor = _user(db_session, UserRole.auditor, "qa-auditor")
    agent = _user(db_session, UserRole.agent, "qa-agent")
    ticket = _ticket(db_session, assignee=agent)
    db_session.commit()

    response = client.get("/api/admin/qa-training/queue?channel=web_chat&limit=20", headers=_headers(auditor))
    assert response.status_code == 200, response.text
    payload = response.json()
    sample = next(item for item in payload["samples"] if item["ticket_id"] == ticket.id)

    assert payload["summary"]["total_samples"] >= 1
    assert sample["sample_channel"] == "web_chat"
    assert sample["status"] == "needs_review"
    assert sample["agent_id"] == agent.id
    assert sample["ai_pre_score"] < 80
    assert {"low_ai_confidence", "missing_customer_evidence", "reply_not_evidenced", "handoff_review_required"}.issubset(set(sample["risks"]))


def test_qa_review_writes_training_task_timeline_and_admin_audit(client: TestClient, db_session):
    lead = _user(db_session, UserRole.lead, "qa-lead")
    agent = _user(db_session, UserRole.agent, "qa-agent")
    ticket = _ticket(db_session, assignee=agent, channel=SourceChannel.email)
    lead.team_id = ticket.team_id
    db_session.commit()

    created = client.post(
        "/api/admin/qa-training/reviews",
        headers=_headers(lead),
        json={
            "ticket_id": ticket.id,
            "final_score": 72,
            "risks": ["missing_policy_citation", "knowledge_gap"],
            "feedback": "Agent should cite the current address-update policy before replying.",
            "knowledge_gap_summary": "Address-update policy answer needs a short agent-facing article.",
            "create_training_task": True,
            "coaching_summary": "Coach agent on citing policy before customer reply.",
        },
    )
    assert created.status_code == 200, created.text
    payload = created.json()
    assert payload["ticket_id"] == ticket.id
    assert payload["final_score"] == 72
    assert payload["training_task"]["task_type"] == "knowledge_gap"
    assert payload["training_task"]["status"] == "open"

    timeline = client.get(f"/api/tickets/{ticket.id}/timeline?limit=20", headers=_headers(lead))
    assert timeline.status_code == 200, timeline.text
    ticket_event_items = [item for item in timeline.json()["items"] if item.get("source_type") == "ticket_event"]
    assert any(item.get("field_name") == "qa_review" for item in ticket_event_items)

    assert db_session.query(QAReview).filter(QAReview.ticket_id == ticket.id).count() == 1
    assert db_session.query(QATrainingTask).filter(QATrainingTask.ticket_id == ticket.id, QATrainingTask.task_type == "knowledge_gap").count() == 1
    assert db_session.query(TicketEvent).filter(TicketEvent.ticket_id == ticket.id, TicketEvent.field_name == "qa_review").count() == 1
    assert db_session.query(AdminAuditLog).filter(AdminAuditLog.action == "qa.review.create", AdminAuditLog.target_type == "qa_review").count() == 1

    queue = client.get("/api/admin/qa-training/queue?channel=email&limit=20", headers=_headers(lead))
    assert queue.status_code == 200, queue.text
    reviewed_sample = next(item for item in queue.json()["samples"] if item["ticket_id"] == ticket.id)
    assert reviewed_sample["status"] == "reviewed"
    assert reviewed_sample["knowledge_gap_summary"].startswith("Address-update policy answer")


def test_auditor_can_read_but_cannot_create_qa_review(client: TestClient, db_session):
    auditor = _user(db_session, UserRole.auditor, "qa-readonly")
    ticket = _ticket(db_session)
    db_session.commit()

    read_response = client.get("/api/admin/qa-training/training-tasks", headers=_headers(auditor))
    assert read_response.status_code == 200, read_response.text

    write_response = client.post(
        "/api/admin/qa-training/reviews",
        headers=_headers(auditor),
        json={"ticket_id": ticket.id, "final_score": 80, "risks": [], "feedback": "Read-only role should not write."},
    )
    assert write_response.status_code == 403
    assert write_response.json()["detail"] == "qa_training_manage_requires_capability"
