from __future__ import annotations

import os
import sys
import json
from pathlib import Path

import pytest
from fastapi import HTTPException
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
from app.enums import ConversationState, EventType, JobStatus, MessageStatus, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole  # noqa: E402
from app.models import BackgroundJob, ChannelAccount, Customer, Ticket, TicketEvent, TicketOutboundMessage, User  # noqa: E402
from app.services import message_dispatch, webchat_ai_safe_service, webchat_ai_service  # noqa: E402
from app.operator_models import OperatorTask  # noqa: E402
from app.services.permissions import CAP_WEBCHAT_HANDOFF_FORCE_TAKEOVER, resolve_capabilities  # noqa: E402
from app.services.webchat_ai_turn_service import schedule_webchat_ai_turn  # noqa: E402
from app.services.webchat_handoff_service import (  # noqa: E402
    accept_handoff_request,
    decline_handoff_request,
    force_takeover_ticket,
    list_handoff_queue,
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


def make_whatsapp_webchat(db) -> tuple[Ticket, WebchatConversation, WebchatMessage, ChannelAccount]:
    whatsapp_handle = "wa-test-contact"
    customer = Customer(name="WhatsApp Visitor", external_ref="whatsapp-visitor", phone=None)
    db.add(customer)
    db.flush()
    account = ChannelAccount(provider="whatsapp", account_id="wa-main", display_name="WhatsApp Main", is_active=True, priority=10)
    db.add(account)
    db.flush()
    ticket = Ticket(
        ticket_no=f"WA-{customer.id}",
        title="WhatsApp inbox reply test",
        description="native whatsapp synthetic inbound",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.whatsapp,
        priority=TicketPriority.medium,
        status=TicketStatus.pending_assignment,
        conversation_state=ConversationState.human_review_required,
        channel_account_id=account.id,
        source_chat_id=whatsapp_handle,
        preferred_reply_channel=SourceChannel.whatsapp.value,
        preferred_reply_contact=whatsapp_handle,
    )
    db.add(ticket)
    db.flush()
    conversation = WebchatConversation(
        public_id=f"wa_native_{ticket.id}",
        visitor_token_hash="token-hash",
        tenant_key="pytest",
        channel_key="whatsapp",
        origin="whatsapp-native",
        ticket_id=ticket.id,
        visitor_name="WhatsApp Visitor",
        visitor_phone=None,
        visitor_ref=whatsapp_handle,
        status="open",
    )
    db.add(conversation)
    db.flush()
    message = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        direction="visitor",
        body="hello from whatsapp",
        body_text="hello from whatsapp",
        author_label="WhatsApp Visitor",
    )
    db.add(message)
    db.flush()
    return ticket, conversation, message, account


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


def _accept_handoff(db, *, conversation: WebchatConversation, ticket: Ticket, message: WebchatMessage, agent: User) -> None:
    request = request_webchat_handoff(
        db,
        conversation=conversation,
        ticket=ticket,
        source="customer_action",
        trigger_type="card_action",
        reason_code="customer_requested_human_support",
        trigger_message_id=message.id,
    )
    accept_handoff_request(db, request_id=request.id, current_user=agent, note="taking over")


def test_admin_reply_to_whatsapp_inbox_queues_native_outbound(db_session):
    agent = make_user(db_session, "whatsapp_agent", UserRole.manager)
    ticket, conversation, message, _account = make_whatsapp_webchat(db_session)
    _accept_handoff(db_session, conversation=conversation, ticket=ticket, message=message, agent=agent)

    reply = admin_reply(db_session, ticket.id, agent, body="你好", has_fact_evidence=False)

    assert reply["ok"] is True
    agent_message = db_session.query(WebchatMessage).filter(WebchatMessage.id == reply["message"]["id"]).one()
    assert agent_message.delivery_status == "queued"

    outbound = db_session.query(TicketOutboundMessage).filter(TicketOutboundMessage.ticket_id == ticket.id).one()
    assert outbound.channel == SourceChannel.whatsapp
    assert outbound.status == MessageStatus.pending
    assert outbound.body == "你好"
    assert outbound.provider_status == "whatsapp_agent_reply_queued"
    assert outbound.created_by == agent.id
    assert outbound.max_retries == message_dispatch.settings.outbox_max_retries
    assert outbound.provider_message_id == f"nexusdesk-outbound-{outbound.id}"

    event = db_session.query(TicketEvent).filter(TicketEvent.ticket_id == ticket.id, TicketEvent.event_type == EventType.outbound_queued).one()
    payload = json.loads(event.payload_json)
    assert payload["external_send"] is True
    assert payload["reply_channel"] == SourceChannel.whatsapp.value
    assert payload["outbound_message_id"] == outbound.id
    assert payload["webchat_message_id"] == agent_message.id


def test_admin_reply_to_webchat_keeps_local_sent_ack(db_session):
    agent = make_user(db_session, "webchat_ack_agent", UserRole.manager)
    ticket, conversation, message = make_webchat(db_session)
    _accept_handoff(db_session, conversation=conversation, ticket=ticket, message=message, agent=agent)

    reply = admin_reply(db_session, ticket.id, agent, body="Hello from webchat.", has_fact_evidence=False)

    assert reply["ok"] is True
    agent_message = db_session.query(WebchatMessage).filter(WebchatMessage.id == reply["message"]["id"]).one()
    assert agent_message.delivery_status == "sent"

    outbound = db_session.query(TicketOutboundMessage).filter(TicketOutboundMessage.ticket_id == ticket.id).one()
    assert outbound.channel == SourceChannel.web_chat
    assert outbound.status == MessageStatus.sent
    assert outbound.provider_status == "webchat_delivered"
    assert outbound.max_retries == 0
    assert outbound.sent_at is not None

    event = db_session.query(TicketEvent).filter(TicketEvent.ticket_id == ticket.id, TicketEvent.event_type == EventType.outbound_sent).one()
    payload = json.loads(event.payload_json)
    assert payload["external_send"] is False
    assert payload["reply_channel"] == SourceChannel.web_chat.value
    assert payload["outbound_message_id"] == outbound.id
    assert payload["webchat_message_id"] == agent_message.id


def test_worker_processes_whatsapp_admin_reply_pending_row_with_native_sidecar(db_session, monkeypatch):
    agent = make_user(db_session, "whatsapp_worker_agent", UserRole.manager)
    ticket, conversation, message, _account = make_whatsapp_webchat(db_session)
    _accept_handoff(db_session, conversation=conversation, ticket=ticket, message=message, agent=agent)
    admin_reply(db_session, ticket.id, agent, body="你好", has_fact_evidence=False)
    outbound = db_session.query(TicketOutboundMessage).filter(TicketOutboundMessage.ticket_id == ticket.id).one()

    monkeypatch.setattr(message_dispatch.settings, "enable_outbound_dispatch", True)
    monkeypatch.setattr(message_dispatch.settings, "outbound_provider", "native")
    monkeypatch.setattr(message_dispatch.settings, "whatsapp_dispatch_mode", "native_sidecar")
    monkeypatch.setattr(message_dispatch, "log_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(message_dispatch, "_enforce_outbound_safety", lambda *args, **kwargs: True)

    def fake_native(db, *, message, ticket, idempotency_key):
        assert message.id == outbound.id
        assert message.channel == SourceChannel.whatsapp
        assert idempotency_key == f"nexusdesk-outbound-{outbound.id}"
        return MessageStatus.sent, "whatsapp_native_sent", None, {
            "adapter": "whatsapp_native_sidecar",
            "channel": SourceChannel.whatsapp.value,
            "idempotency_key": idempotency_key,
        }

    monkeypatch.setattr(message_dispatch, "dispatch_whatsapp_native_outbound", fake_native)

    processed = message_dispatch.process_outbound_message(db_session, outbound)

    assert processed.status == MessageStatus.sent
    assert processed.provider_status == "whatsapp_native_sent"


def test_whatsapp_safe_ack_requires_review_without_customer_visible_fallback(db_session, monkeypatch):
    ticket, conversation, message, _account = make_whatsapp_webchat(db_session)
    turn, _job = attach_open_ai_turn(db_session, conversation, ticket, message)
    monkeypatch.setattr(webchat_ai_safe_service.settings, "webchat_ai_auto_reply_mode", "safe_ack")

    result = webchat_ai_safe_service.process_webchat_ai_reply_job(
        db_session,
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        visitor_message_id=message.id,
    )

    assert result["status"] == "review_required"
    assert result["message_id"] is None
    assert turn.status == "failed"
    assert conversation.ai_suspended is True
    assert ticket.conversation_state == ConversationState.human_review_required
    assert db_session.query(WebchatMessage).filter(WebchatMessage.conversation_id == conversation.id, WebchatMessage.direction == "agent").count() == 0
    assert db_session.query(TicketOutboundMessage).filter(TicketOutboundMessage.ticket_id == ticket.id).count() == 0


def test_whatsapp_ai_reply_queues_native_outbound(db_session, monkeypatch):
    ticket, conversation, message, _account = make_whatsapp_webchat(db_session)
    message.body = "Can you help me check this parcel later?"
    message.body_text = message.body
    turn, _job = attach_open_ai_turn(db_session, conversation, ticket, message)
    monkeypatch.setattr(webchat_ai_safe_service.settings, "webchat_ai_auto_reply_mode", "safe_ai")

    def fake_generate_ai_reply(**_kwargs):
        webchat_ai_service._LAST_AI_REPLY_SOURCE = "private_ai_runtime"
        webchat_ai_service._LAST_AI_FALLBACK_REASON = None
        webchat_ai_service._LAST_BRIDGE_ELAPSED_MS = 42
        webchat_ai_service._LAST_BRIDGE_EFFECTIVE_TIMEOUT_SECONDS = 12
        webchat_ai_service._LAST_BRIDGE_WAIT_TIMEOUT_MS = 12000
        return "Hi, I can help check that with the information available here."

    monkeypatch.setattr(webchat_ai_service, "_generate_ai_reply", fake_generate_ai_reply)

    result = webchat_ai_safe_service.process_webchat_ai_reply_job(
        db_session,
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        visitor_message_id=message.id,
    )

    assert result["status"] == "done"
    assert result["fallback"] is False
    assert turn.status == "completed"

    agent_message = db_session.query(WebchatMessage).filter(WebchatMessage.conversation_id == conversation.id, WebchatMessage.direction == "agent").one()
    assert agent_message.delivery_status == "queued"

    outbound = db_session.query(TicketOutboundMessage).filter(TicketOutboundMessage.ticket_id == ticket.id).one()
    assert outbound.channel == SourceChannel.whatsapp
    assert outbound.status == MessageStatus.pending
    assert outbound.provider_status == "whatsapp_ai_reply_queued"
    assert outbound.body == "Hi, I can help check that with the information available here."
    assert outbound.max_retries == message_dispatch.settings.outbox_max_retries
    assert outbound.provider_message_id == f"nexusdesk-outbound-{outbound.id}"

    event = db_session.query(TicketEvent).filter(TicketEvent.ticket_id == ticket.id, TicketEvent.event_type == EventType.outbound_queued).one()
    payload = json.loads(event.payload_json)
    assert payload["external_send"] is True
    assert payload["reply_channel"] == SourceChannel.whatsapp.value
    assert payload["outbound_message_id"] == outbound.id
    assert payload["webchat_message_id"] == agent_message.id


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


def test_plain_agent_cannot_force_takeover_ai_active_session(db_session):
    agent = make_user(db_session, "plain_agent", UserRole.agent)
    ticket, conversation, message = make_webchat(db_session)
    ticket.assignee_id = agent.id
    attach_open_ai_turn(db_session, conversation, ticket, message)
    db_session.flush()

    assert CAP_WEBCHAT_HANDOFF_FORCE_TAKEOVER not in resolve_capabilities(agent, db_session)
    queue = list_handoff_queue(db_session, agent, view="ai_active")
    assert queue["permissions"]["can_force_takeover"] is False
    assert queue["items"][0]["can_force_takeover"] is False

    with pytest.raises(HTTPException) as exc:
        force_takeover_ticket(db_session, ticket_id=ticket.id, current_user=agent)
    assert exc.value.status_code == 403


@pytest.mark.parametrize("role", [UserRole.lead, UserRole.manager, UserRole.admin])
def test_supervisor_roles_can_force_takeover_ai_active_session(db_session, role):
    user = make_user(db_session, f"force_{role.value}", role)
    ticket, conversation, message = make_webchat(db_session)
    if role == UserRole.lead:
        ticket.assignee_id = user.id
    attach_open_ai_turn(db_session, conversation, ticket, message)
    db_session.flush()

    assert CAP_WEBCHAT_HANDOFF_FORCE_TAKEOVER in resolve_capabilities(user, db_session)
    queue = list_handoff_queue(db_session, user, view="ai_active")
    assert queue["permissions"]["can_force_takeover"] is True
    assert queue["items"][0]["can_force_takeover"] is True

    forced = force_takeover_ticket(db_session, ticket_id=ticket.id, current_user=user, reason_code="operator_forced_takeover")
    assert forced["status"] == "accepted"
    assert forced["takeover_mode"] == "forced"
    assert forced["can_reply"] is True
    assert conversation.active_agent_id == user.id


def test_force_capability_does_not_bypass_accept_before_reply(db_session):
    manager = make_user(db_session, "reply_manager", UserRole.manager)
    ticket, conversation, message = make_webchat(db_session)
    request_webchat_handoff(
        db_session,
        conversation=conversation,
        ticket=ticket,
        source="ai_auto",
        trigger_type="ai_result_handoff_required",
        reason_code="manual_review_required",
        trigger_message_id=message.id,
    )

    with pytest.raises(HTTPException) as exc:
        admin_reply(db_session, ticket.id, manager, body="I should not bypass accept.", has_fact_evidence=False)
    assert exc.value.status_code == 409
    assert "accepted" in str(exc.value.detail)

    force_takeover_ticket(db_session, ticket_id=ticket.id, current_user=manager, reason_code="operator_forced_takeover")
    reply = admin_reply(db_session, ticket.id, manager, body="I have now taken over.", has_fact_evidence=False)
    assert reply["ok"] is True
    assert db_session.query(WebchatEvent).filter(WebchatEvent.conversation_id == conversation.id, WebchatEvent.event_type == "handoff.force_takeover").count() == 1
    assert db_session.query(WebchatEvent).filter(WebchatEvent.conversation_id == conversation.id, WebchatEvent.event_type == "handoff.agent_reply_sent").count() == 1


def test_direct_reply_to_ai_active_session_requires_force_takeover_first(db_session):
    manager = make_user(db_session, "ai_active_manager", UserRole.manager)
    ticket, conversation, message = make_webchat(db_session)
    attach_open_ai_turn(db_session, conversation, ticket, message)

    with pytest.raises(HTTPException) as exc:
        admin_reply(db_session, ticket.id, manager, body="Reply while AI is still active.", has_fact_evidence=False)
    assert exc.value.status_code == 409
    assert "force takeover" in str(exc.value.detail)

    force_takeover_ticket(db_session, ticket_id=ticket.id, current_user=manager, reason_code="operator_forced_takeover")
    reply = admin_reply(db_session, ticket.id, manager, body="Reply after takeover.", has_fact_evidence=False)
    assert reply["ok"] is True


def test_admin_reply_safety_review_requires_explicit_confirmation(db_session):
    agent = make_user(db_session, "review_agent", UserRole.manager)
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
    accept_handoff_request(db_session, request_id=request.id, current_user=agent, note="taking over")

    body = "Your parcel will arrive today."
    with pytest.raises(HTTPException) as review:
        admin_reply(db_session, ticket.id, agent, body=body, has_fact_evidence=False, confirm_review=False)
    assert review.value.status_code == 409
    assert review.value.detail["safety"]["requires_human_review"] is True
    assert review.value.detail["safety"]["normalized_body"] == body
    assert "logistics factual claim requires evidence" in review.value.detail["safety"]["reasons"][0]

    reply = admin_reply(db_session, ticket.id, agent, body=body, has_fact_evidence=False, confirm_review=True)
    assert reply["ok"] is True
    assert reply["safety"]["level"] == "review"


def test_admin_reply_confirm_review_does_not_bypass_block_level_safety(db_session):
    agent = make_user(db_session, "block_agent", UserRole.manager)
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
    accept_handoff_request(db_session, request_id=request.id, current_user=agent, note="taking over")

    with pytest.raises(HTTPException) as blocked:
        admin_reply(db_session, ticket.id, agent, body="Here is the access token and SECRET_KEY.", has_fact_evidence=True, confirm_review=True)
    assert blocked.value.status_code == 400
    assert blocked.value.detail["safety"]["level"] == "block"
    assert blocked.value.detail["safety"]["requires_human_review"] is False
