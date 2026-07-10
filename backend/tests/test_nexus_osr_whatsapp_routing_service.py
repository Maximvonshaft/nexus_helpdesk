from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/nexus_osr_whatsapp_routing_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models, models_operations_dispatch, models_osr, webchat_models  # noqa: F401,E402
from app.db import Base  # noqa: E402
from app.enums import ConversationState, SourceChannel, TicketPriority, TicketSource, TicketStatus  # noqa: E402
from app.models import Customer, Ticket, TicketEvent  # noqa: E402
from app.models_operations_dispatch import OperationsDispatchOutboxRecord  # noqa: E402
from app.models_osr import WhatsAppRoutingRuleRecord  # noqa: E402
from app.services.nexus_osr.case_context import CaseContext  # noqa: E402
from app.services.nexus_osr.persistence import load_case_context  # noqa: E402
from app.services.nexus_osr.whatsapp_routing_service import (  # noqa: E402
    OSROperationsDispatchStatus,
    route_ticket_to_whatsapp_group,
)


RAW_TRACKING = "CH1234567890"
RAW_PHONE = "+382 67 123 456"
RAW_EMAIL = "customer@example.com"
RAW_ADDRESS = "address 123 Main Street Podgorica"
PROVIDER_GROUP_ID = "120363012345678901@g.us"
FALLBACK_PROVIDER_GROUP_ID = "120363099999999999@g.us"


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "nexus_osr_whatsapp_routing.db"
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


def _ticket(db_session, *, suffix: str = "1", country_code: str = "ME", case_type: str = "signed_not_received") -> Ticket:
    customer = Customer(name="OSR Routing Visitor", external_ref=f"osr-routing-visitor-{suffix}")
    db_session.add(customer)
    db_session.flush()
    ticket = Ticket(
        ticket_no=f"OSR-{country_code}-{suffix.zfill(6)}",
        title="OSR routing test",
        description="OSR routing test",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        status=TicketStatus.pending_assignment,
        conversation_state=ConversationState.ai_active,
        preferred_reply_channel=SourceChannel.web_chat.value,
        preferred_reply_contact="osr-webchat",
        country_code=country_code,
        case_type=case_type,
    )
    db_session.add(ticket)
    db_session.flush()
    return ticket


def _case_context(ticket: Ticket, *, country_code: str = "ME", issue_type: str = "signed_not_received") -> CaseContext:
    return CaseContext(
        conversation_id=1000 + ticket.id,
        ticket_id=ticket.id,
        channel="webchat",
        country_code=country_code,
        issue_type=issue_type,
    ).with_inbound_message(
        f"I did not receive {RAW_TRACKING}. My phone is {RAW_PHONE}; email {RAW_EMAIL}; {RAW_ADDRESS}.",
        channel="webchat",
        country_code=country_code,
    )


def _rule(
    db_session,
    *,
    country_code: str = "ME",
    issue_type: str = "signed_not_received",
    destination_group_id: str = PROVIDER_GROUP_ID,
    fallback_group_id: str | None = None,
    enabled: bool = True,
) -> WhatsAppRoutingRuleRecord:
    row = WhatsAppRoutingRuleRecord(
        country_code=country_code,
        issue_type=issue_type,
        channel="whatsapp",
        destination_group_id=destination_group_id,
        fallback_group_id=fallback_group_id,
        priority=10,
        enabled=enabled,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _events(db_session, ticket: Ticket) -> list[TicketEvent]:
    return (
        db_session.query(TicketEvent)
        .filter(TicketEvent.ticket_id == ticket.id)
        .order_by(TicketEvent.id.asc())
        .all()
    )


def _outbox(db_session) -> list[OperationsDispatchOutboxRecord]:
    return db_session.query(OperationsDispatchOutboxRecord).order_by(OperationsDispatchOutboxRecord.id.asc()).all()


def _latest_event_payload(db_session, ticket: Ticket) -> dict:
    events = _events(db_session, ticket)
    assert events
    return json.loads(events[-1].payload_json)


def _assert_no_raw_sensitive(value: object) -> None:
    dumped = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str) if not isinstance(value, str) else value
    assert RAW_TRACKING not in dumped
    assert RAW_PHONE not in dumped
    assert RAW_EMAIL not in dumped
    assert "123 Main Street" not in dumped
    assert PROVIDER_GROUP_ID not in dumped
    assert FALLBACK_PROVIDER_GROUP_ID not in dumped


def test_matching_rule_durably_enqueues_and_ticket_event_is_audit_reference_only(db_session):
    ticket = _ticket(db_session)
    ctx = _case_context(ticket)
    rule = _rule(db_session)

    result = route_ticket_to_whatsapp_group(db_session, ticket=ticket, case_context=ctx, tenant_id="tenant-me")
    db_session.commit()

    assert result.routed is True
    assert result.status == OSROperationsDispatchStatus.PENDING
    assert result.message_text is None
    assert result.outbox_id is not None
    assert result.enqueue_created is True
    rows = _outbox(db_session)
    assert len(rows) == 1
    row = rows[0]
    assert row.routing_rule_id == rule.id
    assert row.tenant_key == "tenant-me"
    assert row.country_code == "ME"
    assert row.destination_group_key == "whatsapp:me:signed_not_received:destination"
    assert row.destination_group_hash.startswith("sha256:")
    assert row.status == "pending"

    loaded = load_case_context(db_session, ticket_id=ticket.id)
    assert loaded is not None
    assert loaded.routed_group_key == row.destination_group_key

    payload = _latest_event_payload(db_session, ticket)
    assert payload["event"] == "operations_dispatch_enqueued"
    assert payload["outbox_id"] == row.id
    assert payload["dispatch_key"] == row.dispatch_key
    assert payload["dispatch_status"] == "pending"
    assert "case_context" not in payload
    assert "ticket_no" not in payload
    assert "message_preview" not in payload
    _assert_no_raw_sensitive(payload)
    _assert_no_raw_sensitive({column.name: getattr(row, column.name) for column in row.__table__.columns})


def test_route_idempotency_uses_outbox_unique_record_not_ticket_event_truth(db_session):
    ticket = _ticket(db_session)
    ctx = _case_context(ticket)
    rule = _rule(db_session)

    first = route_ticket_to_whatsapp_group(db_session, ticket=ticket, case_context=ctx, tenant_id="tenant-me")
    original_hash = _outbox(db_session)[0].destination_group_hash

    # A later rule-target edit must not create a second delivery intent for the
    # same case scope. The original outbox snapshot remains the dispatch truth.
    rule.destination_group_id = "120363055555555555@g.us"
    db_session.flush()
    second = route_ticket_to_whatsapp_group(db_session, ticket=ticket, case_context=ctx, tenant_id="tenant-me")

    assert first.status == "pending"
    assert second.status == "pending"
    assert second.outbox_id == first.outbox_id
    assert second.dispatch_key == first.dispatch_key
    assert second.enqueue_created is False
    assert second.event_id is None
    assert len(_outbox(db_session)) == 1
    assert _outbox(db_session)[0].destination_group_hash == original_hash
    assert len(_events(db_session, ticket)) == 1


def test_disabled_exact_rule_fails_closed_without_outbox_or_broader_rule(db_session):
    ticket = _ticket(db_session, case_type="delivery_delay")
    ctx = _case_context(ticket, issue_type="delivery_delay")
    _rule(db_session, issue_type="delivery_delay", enabled=False)
    _rule(db_session, issue_type="general", destination_group_id="120363011111111111@g.us")

    result = route_ticket_to_whatsapp_group(db_session, ticket=ticket, case_context=ctx)

    assert result.routed is False
    assert result.status == "routing_disabled"
    assert result.dispatch_status == "cancelled"
    assert _outbox(db_session) == []
    payload = _latest_event_payload(db_session, ticket)
    assert payload["routing_status"] == "failed_closed"
    assert payload["routing_scope"] == "exact_country_issue_channel"
    _assert_no_raw_sensitive(payload)


def test_no_rule_fails_closed_without_outbox(db_session):
    ticket = _ticket(db_session, case_type="unknown_issue")
    ctx = _case_context(ticket, issue_type="unknown_issue")

    result = route_ticket_to_whatsapp_group(db_session, ticket=ticket, case_context=ctx)

    assert result.routed is False
    assert result.status == "routing_not_configured"
    assert result.dispatch_status == "failed"
    assert _outbox(db_session) == []
    payload = _latest_event_payload(db_session, ticket)
    assert payload["routing_status"] == "failed_closed"
    assert "destination_group_key" not in payload
    _assert_no_raw_sensitive(payload)


def test_country_general_rule_is_allowed_within_same_country(db_session):
    ticket = _ticket(db_session, case_type="address_issue")
    ctx = _case_context(ticket, issue_type="address_issue")
    _rule(db_session, country_code="ME", issue_type="general", destination_group_id="120363022222222222@g.us")

    result = route_ticket_to_whatsapp_group(db_session, ticket=ticket, case_context=ctx)

    assert result.routed is True
    assert result.destination_group_key == "whatsapp:me:general:destination"
    payload = _latest_event_payload(db_session, ticket)
    assert payload["routing_scope"] == "country_general_channel"


def test_global_rule_is_not_used_for_country_scoped_case(db_session):
    ticket = _ticket(db_session, case_type="customs_delay")
    ctx = _case_context(ticket, issue_type="customs_delay")
    _rule(db_session, country_code="GLOBAL", issue_type="customs_delay", destination_group_id="120363033333333333@g.us")
    _rule(db_session, country_code="GLOBAL", issue_type="general", destination_group_id="120363044444444444@g.us")

    result = route_ticket_to_whatsapp_group(db_session, ticket=ticket, case_context=ctx)

    assert result.routed is False
    assert result.status == "routing_not_configured"
    assert _outbox(db_session) == []


def test_routing_never_invokes_injected_dispatcher_and_does_not_generate_text(db_session):
    ticket = _ticket(db_session, case_type="failed_delivery")
    ctx = _case_context(ticket, issue_type="failed_delivery")
    _rule(
        db_session,
        issue_type="failed_delivery",
        fallback_group_id=FALLBACK_PROVIDER_GROUP_ID,
    )

    class _ForbiddenDispatcher:
        calls = 0

        def send_group_message(self, **kwargs):
            self.calls += 1
            raise AssertionError("routing must not call a provider or sidecar")

    dispatcher = _ForbiddenDispatcher()
    result = route_ticket_to_whatsapp_group(
        db_session,
        ticket=ticket,
        case_context=ctx,
        dispatcher=dispatcher,
    )

    assert result.routed is True
    assert result.status == "pending"
    assert result.message_text is None
    assert result.fallback_used is False
    assert dispatcher.calls == 0
    dumped = json.dumps(
        [
            {column.name: getattr(row, column.name) for column in row.__table__.columns}
            for row in _outbox(db_session)
        ]
        + [json.loads(event.payload_json) for event in _events(db_session, ticket)],
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    _assert_no_raw_sensitive(dumped)


def test_tenant_key_is_part_of_dispatch_identity_and_scope(db_session):
    ticket_a = _ticket(db_session, suffix="2", case_type="delivery_delay")
    ticket_b = _ticket(db_session, suffix="3", case_type="delivery_delay")
    ctx_a = _case_context(ticket_a, issue_type="delivery_delay")
    ctx_b = _case_context(ticket_b, issue_type="delivery_delay")
    _rule(db_session, issue_type="delivery_delay")

    first = route_ticket_to_whatsapp_group(db_session, ticket=ticket_a, case_context=ctx_a, tenant_id="tenant-a")
    second = route_ticket_to_whatsapp_group(db_session, ticket=ticket_b, case_context=ctx_b, tenant_id="tenant-b")

    assert first.dispatch_key != second.dispatch_key
    rows = _outbox(db_session)
    assert {row.tenant_key for row in rows} == {"tenant-a", "tenant-b"}
    assert all(row.country_code == "ME" for row in rows)
