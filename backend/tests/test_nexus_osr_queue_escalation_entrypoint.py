from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/nexus_osr_queue_escalation_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models, webchat_models, models_osr, operator_models  # noqa: F401,E402
from app.db import Base  # noqa: E402
from app.enums import ConversationState, SourceChannel, TicketPriority, TicketSource, TicketStatus  # noqa: E402
from app.models import Customer, Ticket  # noqa: E402
from app.models_osr import EscalationPolicyRecord, HumanHoursPolicyRecord, RuntimeDecisionAuditRecord  # noqa: E402
from app.services.nexus_osr.case_context import CaseContext  # noqa: E402
from app.services.nexus_osr.escalation_orchestration_service import (  # noqa: E402
    EscalationOrchestrationAction,
    evaluate_escalation_for_case,
)
from app.services.nexus_osr.queue_key_resolver import resolve_queue_key  # noqa: E402
from app.webchat_models import WebchatConversation  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "nexus_osr_queue_escalation.db"
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


def make_case(db):
    customer = Customer(name="Queue Visitor", external_ref="queue-entrypoint-visitor")
    db.add(customer)
    db.flush()
    ticket = Ticket(
        ticket_no=f"OSR-Q-{customer.id}",
        title="OSR queue entrypoint",
        description="OSR queue entrypoint",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        status=TicketStatus.pending_assignment,
        conversation_state=ConversationState.ai_active,
        preferred_reply_channel=SourceChannel.web_chat.value,
        preferred_reply_contact="webchat",
        country_code="ZZ",
    )
    db.add(ticket)
    db.flush()
    conversation = WebchatConversation(
        public_id=f"osr_queue_{ticket.id}",
        visitor_token_hash="token-hash",
        tenant_key="tenant-a",
        channel_key="webchat",
        ticket_id=ticket.id,
        visitor_name="Queue Visitor",
        status="open",
    )
    db.add(conversation)
    db.flush()
    ctx = CaseContext(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        channel="webchat",
        country_code="ZZ",
        issue_type="delivery_issue",
    )
    return customer, ticket, conversation, ctx


def add_hours(db, *, country_code: str = "GLOBAL", channel: str = "all", queue_key: str = "default"):
    db.add(HumanHoursPolicyRecord(
        country_code=country_code,
        channel=channel,
        queue_key=queue_key,
        timezone_name="UTC",
        working_hours_json={"mon": [["09:00", "18:00"]]},
        holiday_calendar_json=[],
        auto_ticket_when_offline=True,
    ))


def add_escalation(db, risk_key: str, pattern: str, *, max_ai_attempts: int = 0):
    db.add(EscalationPolicyRecord(
        risk_key=risk_key,
        country_code="ZZ",
        channel="webchat",
        trigger_patterns_json=[pattern],
        max_ai_attempts=max_ai_attempts,
        action="handoff_or_ticket",
        forbidden_commitments_json=["do_not_confirm_resolution"],
    ))


def test_queue_key_resolver_uses_global_all_default_queue(db_session):
    add_hours(db_session, country_code="GLOBAL", channel="all", queue_key="default")
    db_session.commit()

    resolved = resolve_queue_key(
        db_session,
        country_code="AA",
        channel="email",
        language="en",
        issue_type="general",
        tenant_id="tenant-a",
    )

    assert resolved.queue_key == "default"
    assert resolved.source == "human_hours_policy"
    assert resolved.match_score == 0
    assert resolved.fallback is False


def test_queue_key_resolver_prefers_country_specific_override(db_session):
    add_hours(db_session, country_code="GLOBAL", channel="all", queue_key="default")
    add_hours(db_session, country_code="ZZ", channel="all", queue_key="zz-support")
    db_session.commit()

    resolved = resolve_queue_key(db_session, country_code="ZZ", channel="email", language="en", issue_type="general", tenant_id="tenant-a")

    assert resolved.queue_key == "zz-support"
    assert resolved.match_score == 20


def test_queue_key_resolver_prefers_channel_specific_override(db_session):
    add_hours(db_session, country_code="GLOBAL", channel="all", queue_key="default")
    add_hours(db_session, country_code="GLOBAL", channel="webchat", queue_key="webchat-support")
    db_session.commit()

    resolved = resolve_queue_key(db_session, country_code="AA", channel="webchat", language="en", issue_type="general", tenant_id="tenant-a")

    assert resolved.queue_key == "webchat-support"
    assert resolved.match_score == 10


def test_entrypoint_continue_ai_writes_audit_without_customer_reply_body(db_session):
    add_hours(db_session, country_code="ZZ", channel="webchat", queue_key="zz-webchat")
    add_escalation(db_session, "compensation", "compensation", max_ai_attempts=2)
    customer, ticket, conversation, ctx = make_case(db_session)
    db_session.commit()

    result = evaluate_escalation_for_case(
        db_session,
        ticket=ticket,
        conversation=conversation,
        case_context=ctx,
        inbound_message="I want compensation",
        country_code="ZZ",
        channel="webchat",
        language="en",
        issue_type="delivery_issue",
        tenant_id="tenant-a",
        ai_attempt_count=1,
        now=datetime.fromisoformat("2026-07-06T10:00:00+00:00"),
        customer=customer,
    )
    row = db_session.query(RuntimeDecisionAuditRecord).first()

    assert result.action == EscalationOrchestrationAction.CONTINUE_AI
    assert result.queue_resolution is not None
    assert result.queue_resolution.queue_key == "zz-webchat"
    assert row is not None
    assert row.decision_json["next_action"] == "reply"
    assert row.decision_json["tool_actions"] == []
    assert "customer_reply" not in row.decision_json


def test_entrypoint_handoff_audit_contains_handoff_id(db_session):
    add_hours(db_session, country_code="ZZ", channel="webchat", queue_key="zz-webchat")
    add_escalation(db_session, "legal_threat", "legal", max_ai_attempts=0)
    customer, ticket, conversation, ctx = make_case(db_session)
    db_session.commit()

    result = evaluate_escalation_for_case(
        db_session,
        ticket=ticket,
        conversation=conversation,
        case_context=ctx,
        inbound_message="This is a legal issue",
        country_code="ZZ",
        channel="webchat",
        language="en",
        issue_type="legal_threat",
        tenant_id="tenant-a",
        now=datetime.fromisoformat("2026-07-06T10:00:00+00:00"),
        customer=customer,
    )
    row = db_session.query(RuntimeDecisionAuditRecord).first()
    tool_actions = row.decision_json["tool_actions"]

    assert result.action == EscalationOrchestrationAction.REQUEST_HANDOFF
    assert result.handoff_request is not None
    assert tool_actions[0]["tool_name"] == "handoff.request.create"
    assert tool_actions[0]["arguments"]["handoff_request_id"] == result.handoff_request.id
    assert "customer_reply" not in row.decision_json


def test_entrypoint_create_ticket_audit_contains_ticket_id(db_session):
    add_hours(db_session, country_code="ZZ", channel="webchat", queue_key="zz-webchat")
    customer, ticket, conversation, ctx = make_case(db_session)
    db_session.commit()

    result = evaluate_escalation_for_case(
        db_session,
        ticket=ticket,
        conversation=conversation,
        case_context=ctx,
        inbound_message="I cannot wait, this is urgent",
        country_code="ZZ",
        channel="webchat",
        language="en",
        issue_type="delivery_issue",
        tenant_id="tenant-a",
        now=datetime.fromisoformat("2026-07-06T10:00:00+00:00"),
        customer=customer,
    )
    row = db_session.query(RuntimeDecisionAuditRecord).first()
    tool_actions = row.decision_json["tool_actions"]

    assert result.action == EscalationOrchestrationAction.CREATE_TICKET_CUSTOMER_CANNOT_WAIT
    assert result.ticket is not None
    assert tool_actions[0]["tool_name"] == "ticket.create"
    assert tool_actions[0]["arguments"]["ticket_id"] == result.ticket.id
    assert "customer_reply" not in row.decision_json
