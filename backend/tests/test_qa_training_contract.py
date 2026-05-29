from __future__ import annotations

import os
import sys
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models as _models  # noqa: E402,F401
from app import operator_models as _operator_models  # noqa: E402,F401
from app import voice_models as _voice_models  # noqa: E402,F401
from app import webchat_models as _webchat_models  # noqa: E402,F401
from app.auth_service import create_access_token  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.enums import ConversationState, EventType, MessageStatus, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AIConfigResource, AdminAuditLog, Customer, Team, Ticket, TicketAIIntake, TicketEvent, TicketOutboundMessage, User  # noqa: E402
from app.operator_models import OperatorTask  # noqa: E402
from app.utils.time import utc_now  # noqa: E402
from app.voice_models import WebchatVoiceSession  # noqa: E402
from app.webchat_models import WebchatAITurn, WebchatConversation, WebchatMessage  # noqa: E402


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


def _user(db_session, *, role: UserRole, team_id: int | None = None, suffix: str = "") -> User:
    row = User(
        username=f"{role.value}_qa{suffix}",
        display_name=f"{role.value.title()} QA",
        email=f"{role.value}.qa{suffix}@example.test",
        password_hash="x",
        role=role,
        team_id=team_id,
        is_active=True,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _ticket(
    db_session,
    *,
    ticket_no: str,
    team_id: int,
    assignee_id: int | None,
    source_channel: SourceChannel,
    status: TicketStatus = TicketStatus.in_progress,
    conversation_state: ConversationState = ConversationState.ai_active,
    ai_confidence: float | None = None,
    missing_fields: str | None = None,
    required_action: str | None = None,
) -> Ticket:
    customer = Customer(name=f"Customer {ticket_no}", email=f"{ticket_no.lower()}@example.test", phone="+41790000000")
    db_session.add(customer)
    db_session.flush()
    row = Ticket(
        ticket_no=ticket_no,
        title=f"{ticket_no} delivery issue",
        description="delivery issue",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=source_channel,
        priority=TicketPriority.high,
        status=status,
        team_id=team_id,
        assignee_id=assignee_id,
        conversation_state=conversation_state,
        ai_confidence=ai_confidence,
        missing_fields=missing_fields,
        required_action=required_action,
        customer_request="Where is my parcel?",
    )
    db_session.add(row)
    db_session.flush()
    return row


def _seed_qa_training(db_session):
    team = Team(name="QA Loop Support", team_type="support")
    db_session.add(team)
    db_session.flush()
    lead = _user(db_session, role=UserRole.lead, team_id=team.id)
    agent = _user(db_session, role=UserRole.agent, team_id=team.id, suffix="_agent")
    webchat_ticket = _ticket(
        db_session,
        ticket_no="QA-001",
        team_id=team.id,
        assignee_id=agent.id,
        source_channel=SourceChannel.web_chat,
        conversation_state=ConversationState.human_review_required,
        ai_confidence=0.62,
        missing_fields="policy citation missing",
        required_action="create knowledge gap before reply",
    )
    email_ticket = _ticket(
        db_session,
        ticket_no="QA-002",
        team_id=team.id,
        assignee_id=agent.id,
        source_channel=SourceChannel.email,
        ai_confidence=0.91,
    )
    voice_ticket = _ticket(
        db_session,
        ticket_no="QA-003",
        team_id=team.id,
        assignee_id=agent.id,
        source_channel=SourceChannel.web_chat,
        ai_confidence=0.88,
    )
    conversation = WebchatConversation(
        public_id="qa_training_conv",
        visitor_token_hash="hash",
        tenant_key="default",
        channel_key="default",
        ticket_id=webchat_ticket.id,
        visitor_name="Taylor",
        visitor_email="taylor@example.test",
        status="open",
    )
    db_session.add(conversation)
    db_session.flush()
    visitor_message = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=webchat_ticket.id,
        direction="visitor",
        body="Can I get compensation?",
        body_text="Can I get compensation?",
        safety_level="review",
        safety_reasons_json='["policy citation missing"]',
    )
    db_session.add(visitor_message)
    db_session.flush()
    db_session.add(
        WebchatAITurn(
            conversation_id=conversation.id,
            ticket_id=webchat_ticket.id,
            trigger_message_id=visitor_message.id,
            status="failed",
            status_reason="provider timeout",
            fallback_reason="policy gap fallback",
            fact_gate_reason="unsupported compensation fact",
            is_public_reply_allowed=False,
            started_at=utc_now() - timedelta(minutes=5),
            completed_at=utc_now(),
        )
    )
    db_session.add(
        WebchatVoiceSession(
            public_id="qa_voice_1",
            conversation_id=conversation.id,
            ticket_id=voice_ticket.id,
            provider="mock",
            provider_room_name="mock-room",
            status="ended",
            mode="visitor_to_agent",
            recording_consent=False,
            transcript_status="disabled",
            summary_status="pending",
            ai_handoff_reason="identity check incomplete",
            accepted_by_user_id=agent.id,
            accepted_at=utc_now() - timedelta(minutes=20),
            ended_at=utc_now() - timedelta(minutes=10),
        )
    )
    db_session.add(
        TicketOutboundMessage(
            ticket_id=email_ticket.id,
            channel=SourceChannel.email,
            status=MessageStatus.dead,
            subject="Delivery update",
            body="Your parcel update",
            provider_status="dead",
            failure_code="smtp_auth_failed",
            failure_reason="SMTP auth failed",
            created_by=agent.id,
            mailbox_thread_id=None,
        )
    )
    db_session.add(TicketAIIntake(ticket_id=webchat_ticket.id, summary="Potential compensation request", confidence=0.62, missing_fields_json='["policy"]', recommended_action="review policy", created_by=agent.id))
    db_session.add(AIConfigResource(resource_key="knowledge.qa.compensation", config_type="knowledge", name="Compensation draft", is_active=True, draft_summary="Draft from QA sample", published_version=0))
    db_session.add(OperatorTask(source_type="qa", source_id="sample-webchat", ticket_id=webchat_ticket.id, task_type="coaching", status="pending", priority=20, assignee_id=lead.id, reason_code="coach_policy_citation"))
    db_session.add(TicketEvent(ticket_id=webchat_ticket.id, actor_id=lead.id, event_type=EventType.field_updated, field_name="qa_review", note="QA sample marked for review"))
    db_session.add(AdminAuditLog(actor_id=lead.id, action="qa.score.preview", target_type="ticket", target_id=webchat_ticket.id, created_at=utc_now()))
    db_session.flush()
    return lead, agent, webchat_ticket


def test_qa_training_lead_contract_uses_real_quality_sources(tmp_path):
    db_file = tmp_path / "qa_training.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)
    db_session = TestingSession()
    lead, _agent, _webchat_ticket = _seed_qa_training(db_session)
    db_session.commit()

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        response = client.get("/api/lite/qa-training", headers=_headers(lead))
        queue_payload = response.json() if response.status_code == 200 else {}
        appeal_sample = next(item for item in queue_payload.get("qa_queue", []) if item["channel"] == "WebChat")
        knowledge_gap_sample = next(item for item in queue_payload.get("knowledge_gaps", []) if item["key"].startswith("sample:"))
        knowledge_gap_response = client.post(
            "/api/lite/qa-training/knowledge-gaps",
            headers=_headers(lead),
            json={
                "gap_key": knowledge_gap_sample["key"],
                "title": knowledge_gap_sample["title"],
                "source": knowledge_gap_sample["source"],
                "ticket_id": knowledge_gap_sample["ticket_id"],
                "channel": knowledge_gap_sample["channel"],
                "sample": knowledge_gap_sample["sample"],
                "summary": knowledge_gap_sample["evidence"],
                "evidence": [knowledge_gap_sample["evidence"]],
            },
        )
        appeal_response = client.post(
            "/api/lite/qa-training/appeals",
            headers=_headers(lead),
            json={
                "sample_key": appeal_sample["key"],
                "ticket_id": appeal_sample["ticket_id"],
                "channel": appeal_sample["channel"],
                "sample": appeal_sample["sample"],
                "current_score": appeal_sample["ai_pre_score"],
                "requested_score": appeal_sample["ai_pre_score"] + 10,
                "reason": "Agent supplied policy evidence and requests lead score review.",
                "evidence": appeal_sample["evidence"],
            },
        )
        followup = client.get("/api/lite/qa-training", headers=_headers(lead))
        appeal_task = db_session.query(OperatorTask).filter(OperatorTask.task_type == "qa_appeal").one()
        knowledge_gap_task = db_session.query(OperatorTask).filter(OperatorTask.task_type == "knowledge_gap").one()
        knowledge_gap_payload = knowledge_gap_response.json() if knowledge_gap_response.status_code == 200 else {}
        knowledge_gap_resource = db_session.query(AIConfigResource).filter(AIConfigResource.id == knowledge_gap_payload.get("resource_id")).one_or_none()
        appeal_task_ticket_id = appeal_task.ticket_id
        appeal_task_source_id = appeal_task.source_id
        appeal_event_count = db_session.query(TicketEvent).filter(TicketEvent.ticket_id == appeal_sample["ticket_id"], TicketEvent.field_name == "qa_agent_appeal").count()
        appeal_audit_count = db_session.query(AdminAuditLog).filter(AdminAuditLog.action == "qa.agent_appeal.submitted", AdminAuditLog.target_id == appeal_task.id).count()
        knowledge_gap_event_count = db_session.query(TicketEvent).filter(TicketEvent.ticket_id == knowledge_gap_sample["ticket_id"], TicketEvent.field_name == "qa_knowledge_gap").count()
        knowledge_gap_audit_count = db_session.query(AdminAuditLog).filter(AdminAuditLog.action == "qa.knowledge_gap.submitted", AdminAuditLog.target_id == knowledge_gap_payload.get("resource_id")).count()
    finally:
        app.dependency_overrides.pop(get_db, None)
        db_session.close()
        Base.metadata.drop_all(engine)

    assert response.status_code == 200, response.text
    assert knowledge_gap_response.status_code == 200, knowledge_gap_response.text
    assert appeal_response.status_code == 200, appeal_response.text
    assert followup.status_code == 200, followup.text
    payload = response.json()
    followup_payload = followup.json()
    kpis = {item["key"]: item for item in payload["kpis"]}
    followup_kpis = {item["key"]: item for item in followup_payload["kpis"]}
    blocks = {item["key"]: item for item in payload["template_blocks"]}
    followup_blocks = {item["key"]: item for item in followup_payload["template_blocks"]}
    channels = {item["channel"] for item in payload["qa_queue"]}
    sample_sources = {item["source"] for item in payload["qa_queue"]}
    task_keys = {item["key"] for item in payload["training_tasks"]}
    gap_sources = {item["source"] for item in payload["knowledge_gaps"]}

    assert payload["role"] == "lead"
    assert "qa.manage" in payload["capabilities"]
    assert kpis["qa_queue"]["value"] >= 3
    assert kpis["safety_reviews"]["value"] >= 2
    assert kpis["ai_failures"]["value"] == 1
    assert kpis["knowledge_gaps"]["value"] >= 2
    assert {"WebCall", "WebChat", "Email"}.issubset(channels)
    assert {"webchat_ai_turns", "webchat_voice_sessions", "ticket_outbound_messages"}.issubset(sample_sources)
    assert any(key.startswith("task:") for key in task_keys)
    assert "knowledge" in gap_sources
    assert blocks["qa-queue"]["status"] == "implemented"
    assert blocks["knowledge-gap"]["status"] == "implemented"
    assert blocks["appeal"]["status"] == "implemented"
    assert payload["facts"]["qa_manage_capability"] is True
    assert payload["facts"]["agent_appeal_write_endpoint"] == "implemented"
    assert payload["facts"]["knowledge_gap_write_endpoint"] == "implemented"

    knowledge_gap_payload = knowledge_gap_response.json()
    assert knowledge_gap_payload["created"] is True
    assert knowledge_gap_payload["status"] == "pending"
    assert knowledge_gap_payload["gap_key"] == knowledge_gap_sample["key"]
    assert knowledge_gap_payload["ticket_id"] == knowledge_gap_sample["ticket_id"]
    assert knowledge_gap_resource is not None
    assert knowledge_gap_resource.config_type == "knowledge"
    assert knowledge_gap_resource.published_version == 0
    assert knowledge_gap_resource.draft_content_json["gap_key"] == knowledge_gap_sample["key"]

    appeal_payload = appeal_response.json()
    assert appeal_payload["created"] is True
    assert appeal_payload["status"] == "pending"
    assert appeal_payload["sample_key"] == appeal_sample["key"]
    assert followup_blocks["appeal"]["status"] == "implemented"
    assert followup_kpis["agent_appeals"]["value"] == 1
    appealed = {item["key"]: item for item in followup_payload["qa_queue"]}[appeal_sample["key"]]
    assert appealed["appeal_status"] == "pending"
    assert appealed["appeal_task_id"] == appeal_payload["task_id"]
    followup_gap = {item["key"]: item for item in followup_payload["knowledge_gaps"]}[knowledge_gap_sample["key"]]
    assert followup_gap["status"] == "pending"
    assert followup_gap["resource_id"] == knowledge_gap_payload["resource_id"]
    assert any(item["key"].startswith("task:") and item["status"] == "pending" for item in followup_payload["training_tasks"])
    assert appeal_task_ticket_id == appeal_sample["ticket_id"]
    assert appeal_task_source_id == appeal_sample["key"]
    assert knowledge_gap_task.ticket_id == knowledge_gap_sample["ticket_id"]
    assert knowledge_gap_task.source_id == knowledge_gap_sample["key"]
    assert appeal_event_count == 1
    assert appeal_audit_count == 1
    assert knowledge_gap_event_count == 1
    assert knowledge_gap_audit_count == 1


def test_qa_training_requires_qa_manage_capability(tmp_path):
    db_file = tmp_path / "qa_training_forbidden.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)
    db_session = TestingSession()
    _lead, agent, webchat_ticket = _seed_qa_training(db_session)
    db_session.commit()

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        response = client.get("/api/lite/qa-training", headers=_headers(agent))
        appeal = client.post(
            "/api/lite/qa-training/appeals",
            headers=_headers(agent),
            json={"sample_key": "webchat-ticket:1", "ticket_id": webchat_ticket.id, "reason": "agent tries direct appeal"},
        )
        knowledge_gap = client.post(
            "/api/lite/qa-training/knowledge-gaps",
            headers=_headers(agent),
            json={"gap_key": "sample:webchat-ticket:1", "title": "Policy citation gap", "ticket_id": webchat_ticket.id},
        )
    finally:
        app.dependency_overrides.pop(get_db, None)
        db_session.close()
        Base.metadata.drop_all(engine)

    assert response.status_code == 403
    assert response.json()["detail"] == "qa_training_requires_capability"
    assert appeal.status_code == 403
    assert appeal.json()["detail"] == "qa_training_requires_capability"
    assert knowledge_gap.status_code == 403
    assert knowledge_gap.json()["detail"] == "qa_training_requires_capability"
