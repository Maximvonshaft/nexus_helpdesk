from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.enums import ConversationState, SourceChannel, TicketPriority, TicketSource, TicketStatus
from app.models import Customer, Ticket
from app.services import webchat_fast_session_service as svc
from app.services.webchat_fast_config import get_webchat_fast_settings
from app.webchat_models import WebchatConversation

pytestmark = pytest.mark.fast_lane_v2_2_2


@pytest.fixture()
def db_session(monkeypatch):
    monkeypatch.delenv("WEBCHAT_FAST_TRACKING_DEDUPE_SCOPE", raising=False)
    get_webchat_fast_settings.cache_clear()
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
        get_webchat_fast_settings.cache_clear()


def _business_state(tracking: str = "SF123456789") -> svc.FastBusinessState:
    return svc.FastBusinessState(
        intent="lost_or_damaged_parcel",
        issue_type="lost_or_damaged_parcel",
        tracking_number=tracking,
        fast_issue_key=f"tracking:{tracking}:intent:lost_or_damaged_parcel",
    )


def _conversation(db, *, tenant: str, channel: str, session: str, email: str) -> WebchatConversation:
    row = WebchatConversation(
        public_id=f"wcf_{tenant}_{channel}_{session}",
        visitor_token_hash=f"hash-{tenant}-{channel}-{session}",
        tenant_key=tenant,
        channel_key=channel,
        origin=svc.FAST_ORIGIN,
        status="open",
        fast_session_id=session,
        visitor_email=email,
    )
    db.add(row)
    db.flush()
    return row


def _ticket(db, *, conversation: WebchatConversation, customer: Customer, tracking: str = "SF123456789") -> Ticket:
    business_state = _business_state(tracking)
    ticket = Ticket(
        ticket_no=f"T-{conversation.tenant_key}-{conversation.channel_key}-{customer.id}",
        title="Existing WebChat handoff",
        description="Existing ticket",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        status=TicketStatus.pending_assignment,
        conversation_state=ConversationState.human_review_required,
        tracking_number=tracking,
        source_chat_id=f"webchat-fast:{conversation.public_id}",
        source_dedupe_key=svc._fast_ticket_source_dedupe_key(conversation=conversation, business_state=business_state),
        case_type=business_state.issue_type,
        preferred_reply_channel=SourceChannel.web_chat.value,
        preferred_reply_contact=conversation.public_id,
    )
    db.add(ticket)
    db.flush()
    return ticket


def _customer_for_conversation(db, conversation: WebchatConversation) -> Customer:
    return svc._find_or_create_customer(db, conversation=conversation)


def test_tracking_dedupe_scope_default_is_tenant_channel_customer(db_session):
    assert get_webchat_fast_settings().tracking_dedupe_scope == "tenant_channel_customer"


def test_tracking_dedupe_scope_rejects_invalid_value(monkeypatch):
    monkeypatch.setenv("WEBCHAT_FAST_TRACKING_DEDUPE_SCOPE", "tenant")
    get_webchat_fast_settings.cache_clear()
    with pytest.raises(RuntimeError, match="WEBCHAT_FAST_TRACKING_DEDUPE_SCOPE"):
        get_webchat_fast_settings()


def test_legacy_tracking_scope_reuses_across_tenant_channel_customer(db_session, monkeypatch):
    monkeypatch.setenv("WEBCHAT_FAST_TRACKING_DEDUPE_SCOPE", "legacy")
    get_webchat_fast_settings.cache_clear()
    original = _conversation(db_session, tenant="tenant-a", channel="website", session="session-a", email="a@example.test")
    original_customer = _customer_for_conversation(db_session, original)
    existing = _ticket(db_session, conversation=original, customer=original_customer)

    incoming = _conversation(db_session, tenant="tenant-b", channel="mobile", session="session-b", email="b@example.test")
    found = svc._find_active_ticket(db_session, conversation=incoming, business_state=_business_state())

    assert found is not None
    assert found.id == existing.id


def test_tenant_channel_scope_reuses_only_same_tenant_channel(db_session, monkeypatch):
    monkeypatch.setenv("WEBCHAT_FAST_TRACKING_DEDUPE_SCOPE", "tenant_channel")
    get_webchat_fast_settings.cache_clear()
    original = _conversation(db_session, tenant="tenant-a", channel="website", session="session-a", email="a@example.test")
    original_customer = _customer_for_conversation(db_session, original)
    existing = _ticket(db_session, conversation=original, customer=original_customer)

    same_channel_other_customer = _conversation(db_session, tenant="tenant-a", channel="website", session="session-b", email="b@example.test")
    other_channel = _conversation(db_session, tenant="tenant-a", channel="mobile", session="session-c", email="c@example.test")

    assert svc._find_active_ticket(db_session, conversation=same_channel_other_customer, business_state=_business_state()).id == existing.id
    assert svc._find_active_ticket(db_session, conversation=other_channel, business_state=_business_state()) is None


def test_tenant_channel_customer_scope_requires_same_customer(db_session, monkeypatch):
    monkeypatch.setenv("WEBCHAT_FAST_TRACKING_DEDUPE_SCOPE", "tenant_channel_customer")
    get_webchat_fast_settings.cache_clear()
    original = _conversation(db_session, tenant="tenant-a", channel="website", session="session-a", email="a@example.test")
    original_customer = _customer_for_conversation(db_session, original)
    existing = _ticket(db_session, conversation=original, customer=original_customer)

    same_customer_conversation = _conversation(db_session, tenant="tenant-a", channel="website", session="session-a", email="a@example.test")
    different_customer = _conversation(db_session, tenant="tenant-a", channel="website", session="session-b", email="b@example.test")

    assert svc._find_active_ticket(db_session, conversation=same_customer_conversation, business_state=_business_state()).id == existing.id
    assert svc._find_active_ticket(db_session, conversation=different_customer, business_state=_business_state()) is None


def test_tenant_channel_customer_scope_does_not_create_cross_customer_ticket(db_session, monkeypatch):
    monkeypatch.setenv("WEBCHAT_FAST_TRACKING_DEDUPE_SCOPE", "tenant_channel_customer")
    get_webchat_fast_settings.cache_clear()
    original = _conversation(db_session, tenant="tenant-a", channel="website", session="session-a", email="a@example.test")
    original_customer = _customer_for_conversation(db_session, original)
    existing = _ticket(db_session, conversation=original, customer=original_customer)

    incoming = _conversation(db_session, tenant="tenant-a", channel="website", session="session-b", email="b@example.test")
    created = svc.get_or_create_fast_ticket(
        db_session,
        conversation=incoming,
        business_state=_business_state(),
        handoff_reason="manual_review_required",
        recommended_agent_action="Review shipment evidence.",
        customer_message="My parcel is lost SF123456789",
    )

    assert created.id != existing.id
    assert db_session.execute(select(Ticket)).scalars().all() == [existing, created]
