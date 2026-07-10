from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/nexus_osr_auto_ticket_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models, webchat_models, models_osr  # noqa: F401,E402
from app.db import Base  # noqa: E402
from app.enums import ConversationState, EventType, SourceChannel, TicketPriority, TicketStatus  # noqa: E402
from app.models import Customer, Ticket, TicketEvent  # noqa: E402
from app.services.nexus_osr import auto_ticket_service  # noqa: E402
from app.services.nexus_osr.auto_ticket_service import create_or_reuse_ticket_from_case_context  # noqa: E402
from app.services.nexus_osr.case_context import CaseContext  # noqa: E402
from app.services.nexus_osr.persistence import load_case_context  # noqa: E402
from app.webchat_models import WebchatConversation  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "nexus_osr_auto_ticket.db"
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


def test_auto_ticket_creates_ticket_and_case_context(db_session):
    customer = Customer(name="Auto Ticket Visitor", external_ref="auto-ticket-visitor")
    db_session.add(customer)
    db_session.flush()
    conversation = WebchatConversation(
        public_id="auto_ticket_wc_1",
        visitor_token_hash="token-hash",
        tenant_key="pytest",
        channel_key="webchat",
        visitor_name="Auto Ticket Visitor",
        status="open",
    )
    db_session.add(conversation)
    db_session.flush()
    ctx = CaseContext(
        conversation_id=conversation.id,
        channel="webchat",
        country_code="ME",
        issue_type="signed_not_received",
    ).with_inbound_message("I did not receive CH1234567890").with_contact_method(
        channel="whatsapp",
        value="+382 67123456",
        source="webchat_form",
    )

    result = create_or_reuse_ticket_from_case_context(
        db_session,
        case_context=ctx,
        customer=customer,
        conversation=conversation,
        source_channel=SourceChannel.web_chat,
    )
    db_session.commit()

    assert result.created is True
    assert result.ticket.ticket_no.startswith("OSR-ME-")
    assert result.ticket.case_type == "signed_not_received"
    assert result.ticket.customer_id == customer.id
    assert result.case_context.ticket_created is True
    assert conversation.ticket_id == result.ticket.id
    assert db_session.query(TicketEvent).filter(TicketEvent.ticket_id == result.ticket.id).count() == 1
    loaded = load_case_context(db_session, conversation_id=conversation.id, ticket_id=result.ticket.id)
    assert loaded is not None
    assert loaded.ticket_created is True


def test_auto_ticket_reuses_existing_conversation_ticket(db_session):
    first = create_or_reuse_ticket_from_case_context(
        db_session,
        case_context=CaseContext(channel="webchat", country_code="ME", issue_type="tracking"),
        source_channel=SourceChannel.web_chat,
    )
    first.ticket.status = TicketStatus.closed
    first.ticket.conversation_state = ConversationState.replied_to_customer
    first.ticket.priority = TicketPriority.low
    conversation = WebchatConversation(
        public_id="auto_ticket_wc_2",
        visitor_token_hash="token-hash-2",
        tenant_key="pytest",
        channel_key="webchat",
        ticket_id=first.ticket.id,
        visitor_name="Auto Ticket Visitor",
        status="open",
    )
    db_session.add(conversation)
    db_session.flush()
    reused = create_or_reuse_ticket_from_case_context(
        db_session,
        case_context=CaseContext(conversation_id=conversation.id, channel="webchat", country_code="ME", issue_type="tracking"),
        conversation=conversation,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.high,
    )

    assert reused.created is False
    assert reused.ticket.id == first.ticket.id
    assert reused.ticket.status == TicketStatus.pending_assignment
    assert reused.ticket.conversation_state == ConversationState.human_review_required
    assert reused.ticket.priority == TicketPriority.high
    assert reused.ticket.required_action == "Review reused Nexus OSR support ticket and follow up with the customer."
    assert reused.customer_visible_summary.startswith("Your existing support ticket")
    reuse_event = (
        db_session.query(TicketEvent)
        .filter(TicketEvent.ticket_id == reused.ticket.id, TicketEvent.note == "Nexus OSR ticket reused")
        .one()
    )
    assert reuse_event.event_type == EventType.field_updated
    assert "human_review_required" in (reuse_event.payload_json or "")


def test_auto_ticket_retries_ticket_no_unique_collision(db_session, monkeypatch):
    first = create_or_reuse_ticket_from_case_context(
        db_session,
        case_context=CaseContext(channel="webchat", country_code="ME", issue_type="tracking"),
        source_channel=SourceChannel.web_chat,
    )
    db_session.commit()
    generated = [first.ticket.ticket_no, "OSR-ME-RETRY-SAFE-0001"]

    def fake_generate_ticket_no(case_context, *, attempt=0):
        return generated.pop(0)

    monkeypatch.setattr(auto_ticket_service, "_generate_ticket_no", fake_generate_ticket_no)

    result = create_or_reuse_ticket_from_case_context(
        db_session,
        case_context=CaseContext(channel="webchat", country_code="ME", issue_type="refund"),
        source_channel=SourceChannel.web_chat,
    )
    db_session.commit()

    assert result.created is True
    assert result.ticket.ticket_no == "OSR-ME-RETRY-SAFE-0001"
    assert db_session.query(Ticket).filter(Ticket.ticket_no == first.ticket.ticket_no).count() == 1
