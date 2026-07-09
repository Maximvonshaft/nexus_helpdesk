from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/nexus_osr_escalation_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models, webchat_models, models_osr, operator_models  # noqa: F401,E402
from app.db import Base  # noqa: E402
from app.enums import ConversationState, SourceChannel, TicketPriority, TicketSource, TicketStatus  # noqa: E402
from app.models import Customer, Ticket, TicketEvent  # noqa: E402
from app.models_osr import EscalationPolicyRecord, HumanHoursPolicyRecord, RuntimeDecisionAuditRecord  # noqa: E402
from app.services.nexus_osr.case_context import CaseContext  # noqa: E402
from app.services.nexus_osr.escalation_orchestration_service import (  # noqa: E402
    EscalationOrchestrationAction,
    evaluate_and_orchestrate_escalation,
)
from app.webchat_models import WebchatConversation, WebchatEvent, WebchatHandoffRequest  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "nexus_osr_escalation.db"
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


def make_case(db, *, conversation_ticket: bool = True):
    customer = Customer(name="OSR Visitor", external_ref="osr-escalation-visitor")
    db.add(customer)
    db.flush()
    ticket = Ticket(
        ticket_no=f"OSR-ESC-{customer.id}",
        title="OSR escalation",
        description="OSR escalation",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        status=TicketStatus.pending_assignment,
        conversation_state=ConversationState.ai_active,
        preferred_reply_channel=SourceChannel.web_chat.value,
        preferred_reply_contact="webchat",
        country_code="ME",
    )
    db.add(ticket)
    db.flush()
    conversation = WebchatConversation(
        public_id=f"osr_escalation_{ticket.id}",
        visitor_token_hash="token-hash",
        tenant_key="pytest",
        channel_key="webchat",
        ticket_id=ticket.id if conversation_ticket else None,
        visitor_name="OSR Visitor",
        status="open",
    )
    db.add(conversation)
    db.flush()
    ctx = CaseContext(
        conversation_id=conversation.id,
        ticket_id=ticket.id if conversation_ticket else None,
        channel="webchat",
        country_code="ME",
        issue_type="delivery_issue",
    )
    return customer, ticket, conversation, ctx


def add_hours(db, *, holiday: bool = False):
    db.add(HumanHoursPolicyRecord(
        country_code="ME",
        channel="webchat",
        queue_key="support",
        timezone_name="UTC",
        working_hours_json={"mon": [["09:00", "18:00"]], "thu": [["09:00", "18:00"]]},
        holiday_calendar_json=["2026-07-09"] if holiday else [],
        auto_ticket_when_offline=True,
    ))


def add_escalation(db, risk_key: str, pattern: str, *, max_ai_attempts: int = 0):
    db.add(EscalationPolicyRecord(
        risk_key=risk_key,
        country_code="ME",
        channel="webchat",
        trigger_patterns_json=[pattern],
        max_ai_attempts=max_ai_attempts,
        action="handoff_or_ticket",
        forbidden_commitments_json=["do_not_confirm_resolution"],
    ))


def test_online_escalation_requests_existing_webchat_handoff(db_session):
    add_hours(db_session)
    add_escalation(db_session, "formal_complaint", "complaint", max_ai_attempts=0)
    customer, ticket, conversation, ctx = make_case(db_session)
    db_session.commit()

    result = evaluate_and_orchestrate_escalation(
        db_session,
        ticket=ticket,
        conversation=conversation,
        case_context=ctx,
        inbound_message="I want to file a complaint now",
        queue_key="support",
        ai_attempt_count=0,
        now=datetime.fromisoformat("2026-07-06T10:00:00+00:00"),
        customer=customer,
    )

    assert result.action == EscalationOrchestrationAction.REQUEST_HANDOFF
    assert result.handoff_request is not None
    assert db_session.query(WebchatHandoffRequest).count() == 1
    assert conversation.ai_suspended is True
    assert ticket.conversation_state == ConversationState.human_review_required
    assert db_session.query(RuntimeDecisionAuditRecord).count() == 1
    assert db_session.query(WebchatEvent).count() >= 1
    assert db_session.query(TicketEvent).filter(TicketEvent.ticket_id == ticket.id).count() >= 1


def test_offline_escalation_creates_or_reuses_ticket(db_session):
    add_hours(db_session)
    add_escalation(db_session, "formal_complaint", "complaint", max_ai_attempts=0)
    customer, ticket, conversation, ctx = make_case(db_session, conversation_ticket=False)
    db_session.commit()

    result = evaluate_and_orchestrate_escalation(
        db_session,
        ticket=ticket,
        conversation=conversation,
        case_context=ctx,
        inbound_message="I want to file a complaint",
        queue_key="support",
        now=datetime.fromisoformat("2026-07-06T20:00:00+00:00"),
        customer=customer,
    )

    assert result.action == EscalationOrchestrationAction.CREATE_TICKET_HIGH_RISK
    assert result.ticket_result is not None
    assert result.ticket_result.ticket.id is not None
    assert conversation.ticket_id == result.ticket_result.ticket.id
    assert db_session.query(RuntimeDecisionAuditRecord).count() == 1


def test_holiday_is_treated_as_offline(db_session):
    add_hours(db_session, holiday=True)
    add_escalation(db_session, "formal_complaint", "complaint", max_ai_attempts=0)
    customer, ticket, conversation, ctx = make_case(db_session, conversation_ticket=False)
    db_session.commit()

    result = evaluate_and_orchestrate_escalation(
        db_session,
        ticket=ticket,
        conversation=conversation,
        case_context=ctx,
        inbound_message="complaint",
        queue_key="support",
        now=datetime.fromisoformat("2026-07-09T10:00:00+00:00"),
        customer=customer,
    )

    assert result.human_availability.reason == "holiday"
    assert result.ticket_result is not None


def test_compensation_before_threshold_continues_ai(db_session):
    add_hours(db_session)
    add_escalation(db_session, "compensation", "compensation", max_ai_attempts=2)
    customer, ticket, conversation, ctx = make_case(db_session)
    db_session.commit()

    result = evaluate_and_orchestrate_escalation(
        db_session,
        ticket=ticket,
        conversation=conversation,
        case_context=ctx,
        inbound_message="I want compensation for this issue",
        queue_key="support",
        ai_attempt_count=1,
        now=datetime.fromisoformat("2026-07-06T10:00:00+00:00"),
        customer=customer,
    )

    assert result.action == EscalationOrchestrationAction.CONTINUE_AI
    assert db_session.query(WebchatHandoffRequest).count() == 0
    assert db_session.query(RuntimeDecisionAuditRecord).count() == 1


def test_compensation_at_threshold_escalates_to_handoff_when_online(db_session):
    add_hours(db_session)
    add_escalation(db_session, "compensation", "compensation", max_ai_attempts=2)
    customer, ticket, conversation, ctx = make_case(db_session)
    db_session.commit()

    result = evaluate_and_orchestrate_escalation(
        db_session,
        ticket=ticket,
        conversation=conversation,
        case_context=ctx,
        inbound_message="I want compensation for this issue",
        queue_key="support",
        ai_attempt_count=2,
        now=datetime.fromisoformat("2026-07-06T10:00:00+00:00"),
        customer=customer,
    )

    assert result.action == EscalationOrchestrationAction.REQUEST_HANDOFF
    assert db_session.query(WebchatHandoffRequest).count() == 1


def test_legal_threat_escalates_immediately(db_session):
    add_hours(db_session)
    add_escalation(db_session, "legal_threat", "lawyer", max_ai_attempts=0)
    customer, ticket, conversation, ctx = make_case(db_session)
    db_session.commit()

    result = evaluate_and_orchestrate_escalation(
        db_session,
        ticket=ticket,
        conversation=conversation,
        case_context=ctx,
        inbound_message="I will contact my lawyer",
        queue_key="support",
        ai_attempt_count=0,
        now=datetime.fromisoformat("2026-07-06T10:00:00+00:00"),
        customer=customer,
    )

    assert result.action == EscalationOrchestrationAction.REQUEST_HANDOFF
    assert result.escalation.risk_key == "legal_threat"


def test_customer_cannot_wait_creates_ticket(db_session):
    add_hours(db_session)
    customer, ticket, conversation, ctx = make_case(db_session, conversation_ticket=False)
    db_session.commit()

    result = evaluate_and_orchestrate_escalation(
        db_session,
        ticket=ticket,
        conversation=conversation,
        case_context=ctx,
        inbound_message="I cannot wait, this is urgent",
        queue_key="support",
        now=datetime.fromisoformat("2026-07-06T10:00:00+00:00"),
        customer=customer,
    )

    assert result.action == EscalationOrchestrationAction.CREATE_TICKET_CUSTOMER_CANNOT_WAIT
    assert result.ticket_result is not None


def test_audit_payload_does_not_store_raw_tracking_phone_or_email(db_session):
    add_hours(db_session)
    customer, ticket, conversation, ctx = make_case(db_session, conversation_ticket=False)
    db_session.commit()
    raw = "I cannot wait. Email user@example.test phone +382 67123456 tracking CH1234567890"

    result = evaluate_and_orchestrate_escalation(
        db_session,
        ticket=ticket,
        conversation=conversation,
        case_context=ctx,
        inbound_message=raw,
        queue_key="support",
        now=datetime.fromisoformat("2026-07-06T20:00:00+00:00"),
        customer=customer,
    )
    row = db_session.query(RuntimeDecisionAuditRecord).first()
    encoded = json.dumps(row.decision_json, ensure_ascii=False) + json.dumps(row.case_context_json, ensure_ascii=False) + json.dumps(result.event_payload, ensure_ascii=False)

    assert "user@example.test" not in encoded
    assert "+382" not in encoded
    assert "CH1234567890" not in encoded
    assert "tracking ending 4567890" not in encoded
