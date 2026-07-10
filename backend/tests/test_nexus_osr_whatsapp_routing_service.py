from __future__ import annotations

import json
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/nexus_osr_whatsapp_routing_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

from app.db import Base
from app.enums import ConversationState, SourceChannel, TicketPriority, TicketSource, TicketStatus
from app.model_registry import register_all_models
from app.models import Customer, Ticket, TicketEvent
from app.models_operations_dispatch import OperationsDispatchOutboxRecord
from app.models_osr import WhatsAppRoutingRuleRecord
from app.services.nexus_osr.case_context import CaseContext
from app.services.nexus_osr.persistence import load_case_context
from app.services.nexus_osr.whatsapp_routing_service import (
    WhatsAppRoutingStatus,
    build_safe_group_message,
    route_ticket_to_whatsapp_group,
)

register_all_models()

RAW_TRACKING = "CH1234567890"
RAW_PHONE = "+382 67 123 456"
RAW_EMAIL = "customer@example.com"
RAW_GROUP_ID = "120363012345678901@g.us"


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'nexus-osr-whatsapp-routing.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _ticket(db_session, *, country_code: str = "ME", case_type: str = "signed_not_received") -> Ticket:
    customer = Customer(name="OSR Routing Visitor", external_ref=f"routing-{country_code}-{case_type}")
    db_session.add(customer)
    db_session.flush()
    ticket = Ticket(
        ticket_no=f"OSR-{country_code}-000001",
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


def _context(ticket: Ticket, *, country_code: str = "ME", issue_type: str = "signed_not_received") -> CaseContext:
    return CaseContext(
        conversation_id=1001,
        ticket_id=ticket.id,
        channel="webchat",
        country_code=country_code,
        issue_type=issue_type,
    ).with_inbound_message(
        f"I did not receive {RAW_TRACKING}; phone {RAW_PHONE}; email {RAW_EMAIL}.",
        channel="webchat",
        country_code=country_code,
    )


def _rule(
    db_session,
    *,
    country_code: str = "ME",
    issue_type: str = "signed_not_received",
    enabled: bool = True,
    destination_group_id: str = RAW_GROUP_ID,
) -> WhatsAppRoutingRuleRecord:
    rule = WhatsAppRoutingRuleRecord(
        country_code=country_code,
        issue_type=issue_type,
        channel="whatsapp",
        destination_group_id=destination_group_id,
        fallback_group_id="120363999999999999@g.us",
        message_template="Ticket {{ticket_no}} for {{safe_tracking_reference}}",
        priority=10,
        enabled=enabled,
    )
    db_session.add(rule)
    db_session.flush()
    return rule


def _events(db_session, ticket_id: int) -> list[TicketEvent]:
    return (
        db_session.query(TicketEvent)
        .filter(TicketEvent.ticket_id == ticket_id)
        .order_by(TicketEvent.id.asc())
        .all()
    )


def _assert_safe(value: object) -> None:
    dumped = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    for raw in (RAW_TRACKING, RAW_PHONE, RAW_EMAIL, RAW_GROUP_ID, "120363999999999999@g.us"):
        assert raw not in dumped


def test_matching_rule_enqueues_one_durable_dispatch_and_safe_audit(db_session):
    ticket = _ticket(db_session)
    context = _context(ticket)
    _rule(db_session)

    result = route_ticket_to_whatsapp_group(
        db_session,
        ticket=ticket,
        case_context=context,
        tenant_id="tenant-me",
    )
    db_session.commit()

    assert result.routed is True
    assert result.status == WhatsAppRoutingStatus.ROUTED
    assert result.dispatch_status == "pending"
    assert result.outbox_id is not None
    assert result.group_key.startswith("provider-group:")
    assert result.group_hash.startswith("sha256:")
    assert result.fallback_used is False

    row = db_session.get(OperationsDispatchOutboxRecord, result.outbox_id)
    assert row is not None
    assert row.tenant_key == "tenant-me"
    assert row.country_code == "ME"
    assert row.channel_key == "whatsapp"
    assert row.destination_group_key == result.group_key
    assert row.destination_group_hash == result.group_hash
    assert row.status == "pending"

    loaded = load_case_context(db_session, ticket_id=ticket.id, tenant_id="tenant-me")
    assert loaded is not None
    assert loaded.routed_group_key == result.group_key

    events = _events(db_session, ticket.id)
    assert len(events) == 1
    payload = json.loads(events[0].payload_json)
    assert payload["event"] == "operations_dispatch_routing"
    assert payload["routing"]["outbox_id"] == result.outbox_id
    assert payload["routing"]["dispatch_status"] == "pending"
    _assert_safe(payload)
    _assert_safe(row.__dict__)


def test_repeated_route_is_idempotent_and_does_not_duplicate_audit(db_session):
    ticket = _ticket(db_session)
    context = _context(ticket)
    _rule(db_session)

    first = route_ticket_to_whatsapp_group(
        db_session,
        ticket=ticket,
        case_context=context,
        tenant_key="tenant-me",
    )
    second = route_ticket_to_whatsapp_group(
        db_session,
        ticket=ticket,
        case_context=context,
        tenant_key="tenant-me",
    )
    db_session.commit()

    assert first.outbox_id == second.outbox_id
    assert first.dispatch_key == second.dispatch_key
    assert db_session.query(OperationsDispatchOutboxRecord).count() == 1
    assert len(_events(db_session, ticket.id)) == 1


def test_no_rule_does_not_widen_to_global_scope(db_session):
    ticket = _ticket(db_session, country_code="ME", case_type="customs_delay")
    context = _context(ticket, country_code="ME", issue_type="customs_delay")
    _rule(db_session, country_code="GLOBAL", issue_type="customs_delay", destination_group_id="global-provider-group")

    result = route_ticket_to_whatsapp_group(db_session, ticket=ticket, case_context=context)

    assert result.routed is False
    assert result.status == WhatsAppRoutingStatus.NO_RULE
    assert result.outbox_id is None
    assert db_session.query(OperationsDispatchOutboxRecord).count() == 0
    payload = json.loads(_events(db_session, ticket.id)[0].payload_json)
    assert payload["routing"]["status"] == "no_rule"
    _assert_safe(payload)


def test_disabled_exact_rule_fails_closed_without_global_fallback(db_session):
    ticket = _ticket(db_session, country_code="ME", case_type="delivery_delay")
    context = _context(ticket, country_code="ME", issue_type="delivery_delay")
    _rule(db_session, country_code="ME", issue_type="delivery_delay", enabled=False)
    _rule(db_session, country_code="GLOBAL", issue_type="delivery_delay", destination_group_id="global-provider-group")

    result = route_ticket_to_whatsapp_group(db_session, ticket=ticket, case_context=context)

    assert result.routed is False
    assert result.status == WhatsAppRoutingStatus.DISABLED_RULE
    assert result.rule_id is not None
    assert db_session.query(OperationsDispatchOutboxRecord).count() == 0
    payload = json.loads(_events(db_session, ticket.id)[0].payload_json)
    assert payload["routing"]["status"] == "disabled_rule"
    _assert_safe(payload)


def test_cross_country_rule_does_not_route(db_session):
    ticket = _ticket(db_session, country_code="ME", case_type="delivery_delay")
    context = _context(ticket, country_code="ME", issue_type="delivery_delay")
    _rule(db_session, country_code="AL", issue_type="delivery_delay", destination_group_id="al-provider-group")

    result = route_ticket_to_whatsapp_group(db_session, ticket=ticket, case_context=context)

    assert result.status == WhatsAppRoutingStatus.NO_RULE
    assert db_session.query(OperationsDispatchOutboxRecord).count() == 0


def test_direct_dispatch_message_and_tenant_conflict_are_forbidden(db_session):
    ticket = _ticket(db_session)
    context = _context(ticket)
    _rule(db_session)

    class Dispatcher:
        def send(self, *, group_id: str, message: str):
            raise AssertionError("must never be called")

    with pytest.raises(RuntimeError, match="direct_whatsapp_dispatch_forbidden"):
        route_ticket_to_whatsapp_group(
            db_session,
            ticket=ticket,
            case_context=context,
            dispatcher=Dispatcher(),
        )
    with pytest.raises(RuntimeError, match="operations_dispatch_message_body_forbidden"):
        route_ticket_to_whatsapp_group(
            db_session,
            ticket=ticket,
            case_context=context,
            message="send this text",
        )
    with pytest.raises(ValueError, match="tenant_scope_conflict"):
        route_ticket_to_whatsapp_group(
            db_session,
            ticket=ticket,
            case_context=context,
            tenant_key="tenant-a",
            tenant_id="tenant-b",
        )
    assert db_session.query(OperationsDispatchOutboxRecord).count() == 0


def test_template_context_is_validated_but_never_persisted(db_session):
    ticket = _ticket(db_session)
    context = _context(ticket)
    _rule(db_session)

    with pytest.raises(ValueError, match="unsupported_template_context"):
        route_ticket_to_whatsapp_group(
            db_session,
            ticket=ticket,
            case_context=context,
            template_context={"raw_customer_message": RAW_EMAIL},
        )

    preview = build_safe_group_message(
        "{{ticket_no}} {{safe_tracking_reference}} {{unsupported}}",
        values={
            "ticket_no": "OSR-ME-000001",
            "safe_tracking_reference": f"{RAW_TRACKING} {RAW_PHONE} {RAW_EMAIL}",
        },
    )
    assert "OSR-ME-000001" in preview
    assert "[redacted_tracking]" in preview
    assert "[redacted_phone]" in preview
    assert "[redacted_email]" in preview
    assert "[unsupported_template_value]" in preview
    assert db_session.query(OperationsDispatchOutboxRecord).count() == 0
