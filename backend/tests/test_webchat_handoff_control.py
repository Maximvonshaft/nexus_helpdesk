from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_handoff_control_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models, operator_models, webchat_fast_models, webchat_models  # noqa: F401,E402
from app.db import Base  # noqa: E402
from app.enums import ConversationState, JobStatus, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole  # noqa: E402
from app.models import BackgroundJob, Customer, Ticket, User  # noqa: E402
from app.operator_models import OperatorTask  # noqa: E402
from app.services.webchat_ai_turn_service import schedule_webchat_ai_turn  # noqa: E402
from app.services.webchat_handoff_service import (  # noqa: E402
    accept_handoff_request,
    decline_handoff_request,
    force_takeover_ticket,
    release_handoff_request,
    request_webchat_handoff,
    resume_ai_for_handoff,
)
from app.services.webchat_service import admin_reply  # noqa: E402
from app.webchat_models import WebchatAITurn, WebchatConversation, WebchatEvent, WebchatHandoffDecision, WebchatMessage  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "webchat_handoff_control.db"
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


def make_user(db, username: str, role: UserRole = UserRole.admin) -> User:
    row = User(username=username, display_name=username.title(), email=f"{username}@example.test", password_hash="x", role=role, is_active=True)
    db.add(row)
    db.flush()
    return row


def make_webchat(db) -> tuple[Ticket, WebchatConversation, WebchatMessage]:
    customer = Customer(name="Handoff Visitor", external_ref="handoff-visitor")
    db.add(customer)
    db.flush()
    ticket = Ticket(
        ticket_no=f"HH-{customer.id}",
        title="WebChat handoff test",
        description="handoff test",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        status=TicketStatus.pending_assignment,
        conversation_state=ConversationState.ai_active,
        preferred_reply_channel=SourceChannel.web_chat.value,
        preferred_reply_contact="wc_handoff",
    )
    db.add(ticket)
    db.flush()
    conversation = WebchatConversation(
        public_id=f"wc_handoff_{ticket.id}",
        visitor_token_hash="token-hash",
        tenant_key="pytest",
        channel_key="website",
        ticket_id=ticket.id,
        visitor_name="Handoff Visitor",
        status="open",
    )
    db.add(conversation)
    db.flush()
    message = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        direction="visitor",
        body="I need a human agent",
        body_text="I need a human agent",
        author_label="Visitor",
    )
    db.add(message)
    db.flush()
    return ticket, conversation, message


def attach_open_ai_turn(db, conversation: WebchatConversation, ticket: Ticket, message: WebchatMessage) -> tuple[WebchatAITurn, BackgroundJob]:
    job = BackgroundJob(
        queue_name="webchat_ai_reply",
        job_type="webchat.ai_reply",
        payload_json="{}",
        dedupe_key=f"webchat-ai-turn-test:{message.id}",
        status=JobStatus.pending,
    )
    db.add(job)
    db.flush()
    turn = WebchatAITurn(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        trigger_message_id=message.id,
        latest_visitor_message_id=message.id,
        job_id=job.id,
        status="queued",
        is_public_reply_allowed=True,
    )
    db.add(turn)
    db.flush()
    conversation.active_ai_turn_id = turn.id
    conversation.active_ai_status = "queued"
    conversation.active_ai_for_message_id = message.id
    db.flush()
    return turn, job


def test_request_handoff_creates_traceable_queue_and_suspends_ai(db_session):
    ticket, conversation, message = make_webchat(db_session)
    turn, job = attach_open_ai_turn(db_session, conversation, ticket, message)

    request = request_webchat_handoff(
        db_session,
        conversation=conversation,
        ticket=ticket,
        source="ai_auto",
        trigger_type="ai_result_handoff_required",
        reason_code="manual_review_required",
        recommended_agent_action="Review and reply.",
        trigger_message_id=message.id,
        ai_turn_id=turn.id,
    )
    db_session.commit()

    assert request.status == "requested"
    assert conversation.current_handoff_request_id == request.id
    assert conversation.handoff_status == "requested"
    assert conversation.ai_suspended is True
    assert ticket.conversation_state == ConversationState.human_review_required
    assert turn.status == "cancelled"
    assert turn.is_public_reply_allowed is False
    assert job.status == JobStatus.dead
    assert db_session.query(OperatorTask).filter(OperatorTask.webchat_conversation_id == conversation.id, OperatorTask.task_type == "handoff").count() == 1
    assert db_session.query(WebchatEvent).filter(WebchatEvent.conversation_id == conversation.id, WebchatEvent.event_type == "handoff.requested").count() >= 1


def test_decline_accept_release_and_resume_ai_state_machine(db_session):
    admin = make_user(db_session, "handoff_admin")
    ticket, conversation, message = make_webchat(db_session)
    request = request_webchat_handoff(
        db_session,
        conversation=conversation,
        ticket=ticket,
        source="customer_action",
        trigger_type="card_action",
        reason_code="customer_requested_human_support",
        trigger_message_id=message.id,
    )

    declined = decline_handoff_request(db_session, request_id=request.id, current_user=admin, reason_code="busy", note="skip")
    assert declined["status"] == "requested"
    assert db_session.query(WebchatHandoffDecision).filter(WebchatHandoffDecision.request_id == request.id, WebchatHandoffDecision.actor_id == admin.id).count() == 1

    accepted = accept_handoff_request(db_session, request_id=request.id, current_user=admin, note="taking over")
    assert accepted["status"] == "accepted"
    assert conversation.active_agent_id == admin.id
    assert ticket.assignee_id == admin.id
    assert ticket.conversation_state == ConversationState.human_owned

    released = release_handoff_request(db_session, request_id=request.id, current_user=admin, note="return to queue")
    assert released["status"] == "requested"
    assert conversation.active_agent_id is None
    assert conversation.ai_suspended is True

    resumed = resume_ai_for_handoff(db_session, request_id=request.id, current_user=admin, note="AI can continue")
    assert resumed["status"] == "resumed_ai"
    assert conversation.current_handoff_request_id is None
    assert conversation.handoff_status == "none"
    assert conversation.ai_suspended is False
    assert ticket.conversation_state == ConversationState.ai_active


def test_force_takeover_blocks_new_ai_turns_and_agent_reply_is_audited(db_session):
    admin = make_user(db_session, "force_admin")
    ticket, conversation, message = make_webchat(db_session)
    attach_open_ai_turn(db_session, conversation, ticket, message)

    forced = force_takeover_ticket(db_session, ticket_id=ticket.id, current_user=admin, reason_code="operator_forced_takeover")
    assert forced["status"] == "accepted"
    assert forced["takeover_mode"] == "forced"
    assert conversation.ai_suspended is True

    next_message = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        direction="visitor",
        body="Are you there?",
        body_text="Are you there?",
        author_label="Visitor",
    )
    db_session.add(next_message)
    db_session.flush()

    def fail_create_job(*args, **kwargs):
        raise AssertionError("handoff-owned conversation must not create AI jobs")

    snapshot = schedule_webchat_ai_turn(
        db_session,
        conversation=conversation,
        ticket_id=ticket.id,
        visitor_message=next_message,
        create_job=fail_create_job,
    )
    assert snapshot["ai_suppressed_by_handoff"] is True

    reply = admin_reply(db_session, ticket.id, admin, body="I have taken over and will help from here.", has_fact_evidence=False)
    assert reply["ok"] is True
    agent_message = db_session.query(WebchatMessage).filter(WebchatMessage.id == reply["message"]["id"]).one()
    assert agent_message.author_user_id == admin.id
    assert db_session.query(WebchatEvent).filter(WebchatEvent.conversation_id == conversation.id, WebchatEvent.event_type == "handoff.agent_reply_sent").count() == 1
