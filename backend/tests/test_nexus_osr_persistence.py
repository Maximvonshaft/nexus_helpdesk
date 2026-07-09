from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/nexus_osr_persistence_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models, webchat_models, models_osr  # noqa: F401,E402
from app.db import Base  # noqa: E402
from app.enums import ConversationState, SourceChannel, TicketPriority, TicketSource, TicketStatus  # noqa: E402
from app.models import Customer, Ticket  # noqa: E402
from app.models_osr import (  # noqa: E402
    EscalationPolicyRecord,
    HumanHoursPolicyRecord,
    ToolExecutionPolicyRecord,
    WhatsAppRoutingRuleRecord,
)
from app.services.nexus_osr.case_context import CaseContext  # noqa: E402
from app.services.nexus_osr.persistence import (  # noqa: E402
    audit_runtime_decision,
    load_case_context,
    load_escalation_policies,
    resolve_human_hours_policy,
    resolve_tool_execution_policy,
    resolve_whatsapp_routing_rule,
    save_case_context,
)
from app.services.nexus_osr.runtime_decision_contract import (  # noqa: E402
    BusinessReplyType,
    EvidenceSource,
    EvidenceType,
    RuntimeAction,
    RuntimeDecision,
    evaluate_runtime_decision,
)
from app.webchat_models import WebchatConversation  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "nexus_osr_persistence.db"
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


def make_ticket_and_conversation(db):
    customer = Customer(name="OSR Visitor", external_ref="osr-visitor")
    db.add(customer)
    db.flush()
    ticket = Ticket(
        ticket_no=f"OSR-{customer.id}",
        title="OSR test",
        description="OSR test",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        status=TicketStatus.pending_assignment,
        conversation_state=ConversationState.ai_active,
        preferred_reply_channel=SourceChannel.web_chat.value,
        preferred_reply_contact="osr-webchat",
        country_code="ME",
    )
    db.add(ticket)
    db.flush()
    conversation = WebchatConversation(
        public_id=f"osr_wc_{ticket.id}",
        visitor_token_hash="token-hash",
        tenant_key="pytest",
        channel_key="website",
        ticket_id=ticket.id,
        visitor_name="OSR Visitor",
        status="open",
    )
    db.add(conversation)
    db.flush()
    return ticket, conversation


def test_case_context_persists_and_loads_short_lived_state(db_session):
    ticket, conversation = make_ticket_and_conversation(db_session)
    ctx = CaseContext(conversation_id=conversation.id, ticket_id=ticket.id, channel="webchat", country_code="ME")
    ctx = ctx.with_inbound_message("Please check CH1234567890").with_contact_method(
        channel="whatsapp",
        value="+382 67123456",
        source="webchat_form",
    ).mark_ticket_created(ticket.id)

    row = save_case_context(db_session, ctx, tenant_id="pytest")
    db_session.commit()
    loaded = load_case_context(db_session, conversation_id=conversation.id, ticket_id=ticket.id)

    assert row.id is not None
    assert loaded is not None
    assert loaded.ticket_created is True
    assert loaded.tracking_number_hash
    assert loaded.contact_methods[0].value_redacted == "[redacted_phone]"


def test_scoped_policy_resolution_prefers_country_and_channel_specific_rows(db_session):
    db_session.add(HumanHoursPolicyRecord(
        country_code="GLOBAL",
        channel="all",
        queue_key="support",
        timezone_name="UTC",
        working_hours_json={"mon": [["00:00", "01:00"]]},
    ))
    db_session.add(HumanHoursPolicyRecord(
        country_code="ME",
        channel="webchat",
        queue_key="support",
        timezone_name="Europe/Podgorica",
        working_hours_json={"mon": [["09:00", "18:00"]]},
    ))
    db_session.commit()

    policy = resolve_human_hours_policy(db_session, country_code="ME", channel="webchat", queue_key="support")

    assert policy is not None
    assert policy.timezone_name == "Europe/Podgorica"
    assert policy.weekly_hours["mon"] == [("09:00", "18:00")]


def test_escalation_and_tool_policies_load_from_database(db_session):
    db_session.add(EscalationPolicyRecord(
        risk_key="compensation",
        country_code="ME",
        channel="webchat",
        trigger_patterns_json=["compensation", "赔偿"],
        max_ai_attempts=1,
        action="handoff_or_ticket",
        forbidden_commitments_json=["do_not_confirm_refund"],
    ))
    db_session.add(ToolExecutionPolicyRecord(
        tool_name="ticket.create",
        country_code="ME",
        channel="webchat",
        enabled=True,
        ai_auto_executable=True,
        requires_tracking_number=True,
        requires_contact=True,
        allowed_channels_json=["webchat"],
        allowed_countries_json=["ME"],
    ))
    db_session.commit()

    escalations = load_escalation_policies(db_session, country_code="ME", channel="webchat")
    tool_policy = resolve_tool_execution_policy(db_session, tool_name="ticket.create", country_code="ME", channel="webchat")

    assert escalations[0].risk_key == "compensation"
    assert escalations[0].max_ai_attempts == 1
    assert tool_policy is not None
    assert tool_policy.requires_tracking_number is True
    assert tool_policy.allowed_channels == {"webchat"}


def test_whatsapp_routing_rule_resolution(db_session):
    db_session.add(WhatsAppRoutingRuleRecord(
        country_code="ME",
        issue_type="signed_not_received",
        channel="whatsapp",
        destination_group_id="wa-group-me-delivery",
        fallback_group_id="wa-group-me-fallback",
        priority=10,
    ))
    db_session.commit()

    rule = resolve_whatsapp_routing_rule(db_session, country_code="ME", issue_type="signed_not_received")

    assert rule is not None
    assert rule.destination_group_id == "wa-group-me-delivery"


def test_runtime_decision_audit_persists_contract_snapshot(db_session):
    ticket, conversation = make_ticket_and_conversation(db_session)
    ctx = CaseContext(conversation_id=conversation.id, ticket_id=ticket.id, channel="webchat", country_code="ME")
    decision = RuntimeDecision(
        business_reply_type=BusinessReplyType.TRACKING_STATUS_ANSWER,
        next_action=RuntimeAction.REPLY,
        customer_reply="Your parcel is out for delivery.",
        evidence_sources=[EvidenceSource(
            EvidenceType.MCP_CURRENT_STATUS,
            source_id="mcp:order-query",
            label="MCP order query",
            verified=True,
            current_status=True,
        )],
    )
    evaluation = evaluate_runtime_decision(decision)

    row = audit_runtime_decision(
        db_session,
        decision=decision,
        evaluation=evaluation,
        case_context=ctx,
        tenant_id="pytest",
        channel="webchat",
        country_code="ME",
        conversation_id=conversation.id,
        ticket_id=ticket.id,
    )
    db_session.commit()

    assert row.id is not None
    assert row.allowed is True
    assert row.decision_json["business_reply_type"] == "tracking_status_answer"
    assert row.case_context_json["country_code"] == "ME"
