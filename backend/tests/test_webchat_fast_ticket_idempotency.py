from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.enums import ConversationState, SourceChannel, TicketPriority, TicketSource, TicketStatus
from app.models import Customer, Ticket
from app.services import webchat_fast_session_service as svc
from app.webchat_models import WebchatConversation, WebchatMessage

pytestmark = pytest.mark.fast_lane_v2_2_2


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_tickets_source_dedupe_key ON tickets(source_dedupe_key) WHERE source_dedupe_key IS NOT NULL"))
    Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _conversation(db_session) -> WebchatConversation:
    row = WebchatConversation(
        public_id="wcf_ticket_race",
        visitor_token_hash="visitor-hash",
        tenant_key="default",
        channel_key="website",
        origin=svc.FAST_ORIGIN,
        status="open",
        fast_session_id="ticket-race-session",
        visitor_email="race@example.test",
    )
    db_session.add(row)
    db_session.flush()
    return row


def _business_state() -> svc.FastBusinessState:
    return svc.FastBusinessState(
        intent="lost_or_damaged_parcel",
        issue_type="lost_or_damaged_parcel",
        tracking_number="SF123456789",
        fast_issue_key="tracking:SF123456789:intent:lost_or_damaged_parcel",
    )


def _existing_ticket(db_session, *, dedupe_key: str) -> Ticket:
    customer = Customer(name="Existing Customer", email="existing@example.test", email_normalized="existing@example.test")
    db_session.add(customer)
    db_session.flush()
    ticket = Ticket(
        ticket_no="T-EXISTING-RACE",
        title="Existing WebChat handoff",
        description="Existing ticket created by the winning concurrent request.",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        status=TicketStatus.pending_assignment,
        conversation_state=ConversationState.human_review_required,
        tracking_number="SF123456789",
        source_chat_id="webchat-fast:wcf_ticket_race",
        source_dedupe_key=dedupe_key,
        case_type="lost_or_damaged_parcel",
        preferred_reply_channel=SourceChannel.web_chat.value,
        preferred_reply_contact="wcf_ticket_race",
    )
    db_session.add(ticket)
    db_session.flush()
    return ticket


def test_get_or_create_fast_ticket_refetches_winner_after_source_dedupe_conflict(db_session, monkeypatch):
    conversation = _conversation(db_session)
    visitor_message = WebchatMessage(
        conversation_id=conversation.id,
        ticket_id=None,
        direction="visitor",
        body="My parcel is missing SF123456789",
        body_text="My parcel is missing SF123456789",
        message_type="text",
        client_message_id="client-ticket-race",
        delivery_status="sent",
        author_label="Customer",
    )
    db_session.add(visitor_message)
    business_state = _business_state()
    dedupe_key = svc._fast_ticket_source_dedupe_key(conversation=conversation, business_state=business_state)
    existing = _existing_ticket(db_session, dedupe_key=dedupe_key)
    db_session.flush()

    original_find_active_ticket = svc._find_active_ticket
    calls = {"count": 0}

    def fake_find_active_ticket(db, *, conversation: WebchatConversation, business_state: svc.FastBusinessState):
        calls["count"] += 1
        if calls["count"] == 1:
            return None
        return original_find_active_ticket(db, conversation=conversation, business_state=business_state)

    monkeypatch.setattr(svc, "_find_active_ticket", fake_find_active_ticket)

    returned = svc.get_or_create_fast_ticket(
        db_session,
        conversation=conversation,
        business_state=business_state,
        handoff_reason="server_policy_handoff_required",
        recommended_agent_action="Review shipment evidence.",
        customer_message="My parcel is missing SF123456789",
    )

    assert returned.id == existing.id
    assert db_session.execute(select(Ticket)).scalars().all() == [existing]
    assert conversation.ticket_id == existing.id
    db_session.refresh(visitor_message)
    assert visitor_message.ticket_id == existing.id
