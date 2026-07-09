from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/nexus_osr_runtime_bridge_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models, webchat_models, models_osr  # noqa: F401,E402
from app.db import Base  # noqa: E402
from app.enums import ConversationState, SourceChannel, TicketPriority, TicketSource, TicketStatus  # noqa: E402
from app.models import Customer, Ticket  # noqa: E402
from app.services.knowledge_retrieval_service import KnowledgeChunkHit  # noqa: E402
from app.services.nexus_osr.case_context import CaseContext  # noqa: E402
from app.services.nexus_osr.runtime_bridge import (  # noqa: E402
    audit_existing_webchat_runtime_decision,
    build_case_context_from_webchat,
    build_runtime_decision_from_existing_runtime,
    evidence_from_knowledge_hits,
    evidence_from_tracking_fact,
    mark_ticket_created_action,
)
from app.services.nexus_osr.runtime_decision_contract import BusinessReplyType, RuntimeAction, evaluate_runtime_decision  # noqa: E402
from app.services.tracking_fact_schema import TrackingFactEvent, TrackingFactResult  # noqa: E402
from app.webchat_models import WebchatConversation, WebchatMessage  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "nexus_osr_runtime_bridge.db"
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


def make_ticket_conversation_message(db):
    customer = Customer(name="Bridge Visitor", external_ref="bridge-visitor")
    db.add(customer)
    db.flush()
    ticket = Ticket(
        ticket_no=f"BR-{customer.id}",
        title="Bridge test",
        description="Bridge test",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        status=TicketStatus.pending_assignment,
        conversation_state=ConversationState.ai_active,
        preferred_reply_channel=SourceChannel.web_chat.value,
        preferred_reply_contact="bridge-webchat",
        country_code="ME",
    )
    db.add(ticket)
    db.flush()
    conversation = WebchatConversation(
        public_id=f"bridge_wc_{ticket.id}",
        visitor_token_hash="token-hash",
        tenant_key="pytest",
        channel_key="webchat",
        ticket_id=ticket.id,
        visitor_name="Bridge Visitor",
        status="open",
    )
    db.add(conversation)
    db.flush()
    message = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        direction="visitor",
        body="Where is CH1234567890?",
        body_text="Where is CH1234567890?",
        author_label="Visitor",
    )
    db.add(message)
    db.flush()
    return ticket, conversation, message


def test_bridge_converts_tracking_fact_to_current_status_evidence():
    fact = TrackingFactResult(
        ok=True,
        tracking_number="CH1234567890",
        status="OUT_FOR_DELIVERY",
        status_label="Out for delivery",
        checked_at="2026-07-09T08:00:00Z",
        tool_status="success",
        pii_redacted=True,
        fact_evidence_present=True,
        latest_event=TrackingFactEvent(description="Out for delivery"),
    )

    evidence = evidence_from_tracking_fact(fact)

    assert len(evidence) == 1
    assert evidence[0].evidence_type == "mcp.current_status"
    assert evidence[0].verified is True
    assert evidence[0].current_status is True
    assert "tracking_number_hash" in evidence[0].summary


def test_bridge_converts_customer_visible_knowledge_hit():
    hit = KnowledgeChunkHit(
        item_id=1,
        item_key="me_service_scope",
        title="Montenegro service scope",
        published_version=3,
        chunk_index=0,
        score=80.0,
        text="We support delivery in Montenegro.",
        metadata={"visibility": "customer", "audience_scope": "customer"},
        retrieval_method="lexical",
        direct_answer="We support delivery in Montenegro.",
        answer_mode="direct_answer",
    )

    evidence = evidence_from_knowledge_hits([hit])

    assert len(evidence) == 1
    assert evidence[0].evidence_type == "knowledge.customer_visible"
    assert evidence[0].customer_visible is True
    assert evidence[0].verified is True


def test_bridge_builds_and_audits_existing_webchat_runtime_decision(db_session):
    ticket, conversation, message = make_ticket_conversation_message(db_session)
    fact = TrackingFactResult(
        ok=True,
        tracking_number="CH1234567890",
        status="OUT_FOR_DELIVERY",
        status_label="Out for delivery",
        checked_at="2026-07-09T08:00:00Z",
        tool_status="success",
        pii_redacted=True,
        fact_evidence_present=True,
    )
    ctx = build_case_context_from_webchat(
        db_session,
        ticket=ticket,
        conversation=conversation,
        visitor_message=message,
        tracking_fact=fact,
        issue_type="tracking",
    )
    decision = build_runtime_decision_from_existing_runtime(
        business_reply_type=BusinessReplyType.TRACKING_STATUS_ANSWER,
        next_action=RuntimeAction.REPLY,
        customer_reply="Your parcel is out for delivery.",
        tracking_fact=fact,
        case_context=ctx,
    )
    evaluation = evaluate_runtime_decision(decision)
    audit = audit_existing_webchat_runtime_decision(db_session, ticket=ticket, conversation=conversation, decision=decision, case_context=ctx)
    db_session.commit()

    assert evaluation.allowed
    assert audit.id is not None
    assert audit.allowed is True
    assert audit.decision_json["business_reply_type"] == "tracking_status_answer"
    assert audit.case_context_json["safe_tracking_reference"] is not None


def test_ticket_created_notice_requires_marked_executed_action():
    decision = build_runtime_decision_from_existing_runtime(
        business_reply_type=BusinessReplyType.TICKET_CREATED_NOTICE,
        next_action=RuntimeAction.REPLY,
        customer_reply="Your ticket has been created.",
        case_context=CaseContext(ticket_id="T-1", conversation_id="C-1"),
    )
    blocked = evaluate_runtime_decision(decision)
    allowed = evaluate_runtime_decision(mark_ticket_created_action(decision, ticket_id="T-1"))

    assert not blocked.allowed
    assert any(item.code == "ticket_created_notice_without_ticket_create_action" for item in blocked.violations)
    assert allowed.allowed
