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

from app import models, webchat_models, models_osr  # noqa: F401,E402
from app.db import Base  # noqa: E402
from app.enums import ConversationState, SourceChannel, TicketPriority, TicketSource, TicketStatus  # noqa: E402
from app.models import Customer, Ticket, TicketEvent  # noqa: E402
from app.models_osr import WhatsAppRoutingRuleRecord  # noqa: E402
from app.services.nexus_osr.case_context import CaseContext  # noqa: E402
from app.services.nexus_osr.persistence import load_case_context  # noqa: E402
from app.services.nexus_osr.whatsapp_routing_service import (  # noqa: E402
    OSROperationsDispatchStatus,
    WhatsAppDispatchResult,
    route_ticket_to_whatsapp_group,
)


RAW_TRACKING = "CH1234567890"
RAW_PHONE = "+382 67 123 456"
RAW_EMAIL = "customer@example.com"
RAW_ADDRESS = "address 123 Main Street Podgorica"
PROVIDER_GROUP_ID = "provider-me-delivery-group-raw-id"
FALLBACK_PROVIDER_GROUP_ID = "provider-me-fallback-group-raw-id"


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


def _ticket(db_session, *, country_code: str = "ME", case_type: str = "signed_not_received") -> Ticket:
    customer = Customer(name="OSR Routing Visitor", external_ref="osr-routing-visitor")
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


def _case_context(ticket: Ticket, *, country_code: str = "ME", issue_type: str = "signed_not_received") -> CaseContext:
    return CaseContext(
        conversation_id=1001,
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
    message_template: str | None = None,
) -> WhatsAppRoutingRuleRecord:
    row = WhatsAppRoutingRuleRecord(
        country_code=country_code,
        issue_type=issue_type,
        channel="whatsapp",
        destination_group_id=destination_group_id,
        fallback_group_id=fallback_group_id,
        message_template=message_template,
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


def _latest_event_payload(db_session, ticket: Ticket) -> dict:
    events = _events(db_session, ticket)
    assert events
    return json.loads(events[-1].payload_json)


def _assert_no_raw_pii(value: object) -> None:
    dumped = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str) if not isinstance(value, str) else value
    assert RAW_TRACKING not in dumped
    assert RAW_PHONE not in dumped
    assert RAW_EMAIL not in dumped
    assert "123 Main Street" not in dumped
    assert PROVIDER_GROUP_ID not in dumped
    assert FALLBACK_PROVIDER_GROUP_ID not in dumped


def test_matching_rule_selects_destination_key_and_writes_pending_dispatch_event(db_session):
    ticket = _ticket(db_session)
    ctx = _case_context(ticket)
    _rule(db_session)

    result = route_ticket_to_whatsapp_group(db_session, ticket=ticket, case_context=ctx, tenant_id="pytest")
    db_session.commit()

    assert result.routed is True
    assert result.status == OSROperationsDispatchStatus.PENDING
    assert result.destination_group_key == "whatsapp:me:signed_not_received:destination"
    assert result.case_context.routed_group_key == "whatsapp:me:signed_not_received:destination"
    loaded = load_case_context(db_session, ticket_id=ticket.id)
    assert loaded is not None
    assert loaded.routed_group_key == "whatsapp:me:signed_not_received:destination"
    payload = _latest_event_payload(db_session, ticket)
    assert payload["event"] == "operations_dispatch_pending"
    assert payload["dispatch_status"] == "pending"
    assert payload["destination_group_key"] == "whatsapp:me:signed_not_received:destination"
    assert payload["provider_group_id_hash"].startswith("sha256:")
    assert payload["routing_scope"] == "exact_country_issue_channel"
    _assert_no_raw_pii(result.message_text or "")
    _assert_no_raw_pii(payload)


def test_route_idempotency_does_not_duplicate_pending_dispatch(db_session):
    ticket = _ticket(db_session)
    ctx = _case_context(ticket)
    _rule(db_session)

    first = route_ticket_to_whatsapp_group(db_session, ticket=ticket, case_context=ctx, tenant_id="pytest")
    second = route_ticket_to_whatsapp_group(db_session, ticket=ticket, case_context=ctx, tenant_id="pytest")

    assert first.status == "pending"
    assert second.status == "pending"
    assert second.event_id == first.event_id
    assert len(_events(db_session, ticket)) == 1


def test_disabled_exact_rule_does_not_route_and_writes_safe_cancelled_event(db_session):
    ticket = _ticket(db_session, case_type="delivery_delay")
    ctx = _case_context(ticket, issue_type="delivery_delay")
    _rule(db_session, issue_type="delivery_delay", destination_group_id=PROVIDER_GROUP_ID, enabled=False)
    _rule(db_session, country_code="GLOBAL", issue_type="general", destination_group_id="provider-global-general")

    result = route_ticket_to_whatsapp_group(db_session, ticket=ticket, case_context=ctx)

    assert result.routed is False
    assert result.status == "routing_disabled"
    assert result.case_context.routed_group_key is None
    payload = _latest_event_payload(db_session, ticket)
    assert payload["event"] == "routing_disabled"
    assert payload["dispatch_status"] == "cancelled"
    assert "destination_group_key" not in payload or payload["destination_group_key"] is None
    _assert_no_raw_pii(payload)


def test_no_rule_writes_routing_not_configured_without_group_dispatch(db_session):
    ticket = _ticket(db_session, case_type="unknown_issue")
    ctx = _case_context(ticket, issue_type="unknown_issue")

    result = route_ticket_to_whatsapp_group(db_session, ticket=ticket, case_context=ctx)

    assert result.routed is False
    assert result.status == "routing_not_configured"
    payload = _latest_event_payload(db_session, ticket)
    assert payload["event"] == "routing_not_configured"
    assert payload["dispatch_status"] == "failed"
    assert payload["case_context"]["routed_group_key"] is None
    _assert_no_raw_pii(payload)


def test_routing_fallback_country_general_channel(db_session):
    ticket = _ticket(db_session, country_code="ME", case_type="address_issue")
    ctx = _case_context(ticket, country_code="ME", issue_type="address_issue")
    _rule(db_session, country_code="ME", issue_type="general", destination_group_id="provider-me-general")

    result = route_ticket_to_whatsapp_group(db_session, ticket=ticket, case_context=ctx)

    assert result.routed is True
    assert result.destination_group_key == "whatsapp:me:general:destination"
    payload = _latest_event_payload(db_session, ticket)
    assert payload["routing_scope"] == "country_general_channel"
    _assert_no_raw_pii(payload)


def test_routing_fallback_global_issue_channel(db_session):
    ticket = _ticket(db_session, country_code="ME", case_type="customs_delay")
    ctx = _case_context(ticket, country_code="ME", issue_type="customs_delay")
    _rule(db_session, country_code="GLOBAL", issue_type="customs_delay", destination_group_id="provider-global-customs")

    result = route_ticket_to_whatsapp_group(db_session, ticket=ticket, case_context=ctx)

    assert result.routed is True
    assert result.destination_group_key == "whatsapp:global:customs_delay:destination"
    payload = _latest_event_payload(db_session, ticket)
    assert payload["routing_scope"] == "global_issue_channel"
    _assert_no_raw_pii(payload)


def test_routing_fallback_global_general_channel(db_session):
    ticket = _ticket(db_session, country_code="ME", case_type="unmapped_issue")
    ctx = _case_context(ticket, country_code="ME", issue_type="unmapped_issue")
    _rule(db_session, country_code="GLOBAL", issue_type="general", destination_group_id="provider-global-general")

    result = route_ticket_to_whatsapp_group(db_session, ticket=ticket, case_context=ctx)

    assert result.routed is True
    assert result.destination_group_key == "whatsapp:global:general:destination"
    payload = _latest_event_payload(db_session, ticket)
    assert payload["routing_scope"] == "global_general_channel"
    _assert_no_raw_pii(payload)


def test_dispatch_failure_uses_fallback_group_and_marks_case_context_routed(db_session):
    ticket = _ticket(db_session, case_type="failed_delivery")
    ctx = _case_context(ticket, issue_type="failed_delivery")
    _rule(
        db_session,
        issue_type="failed_delivery",
        destination_group_id=PROVIDER_GROUP_ID,
        fallback_group_id=FALLBACK_PROVIDER_GROUP_ID,
    )

    class _Dispatcher:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, dict]] = []

        def send_group_message(self, *, provider_group_id: str, message: str, metadata: dict):
            self.calls.append((provider_group_id, message, metadata))
            if len(self.calls) == 1:
                return WhatsAppDispatchResult(ok=False, status="failed", error_code="primary_unavailable", retryable=True)
            return WhatsAppDispatchResult(ok=True, status="sent", external_message_id="fallback-msg-1")

    dispatcher = _Dispatcher()
    result = route_ticket_to_whatsapp_group(db_session, ticket=ticket, case_context=ctx, dispatcher=dispatcher)
    db_session.commit()

    assert result.routed is True
    assert result.status == OSROperationsDispatchStatus.FALLBACK_USED
    assert result.fallback_used is True
    assert result.attempted_group_key == "whatsapp:me:failed_delivery:fallback"
    assert [call[0] for call in dispatcher.calls] == [PROVIDER_GROUP_ID, FALLBACK_PROVIDER_GROUP_ID]
    loaded = load_case_context(db_session, ticket_id=ticket.id)
    assert loaded is not None
    assert loaded.routed_group_key == "whatsapp:me:failed_delivery:fallback"
    payload = _latest_event_payload(db_session, ticket)
    assert payload["event"] == "operations_dispatch_fallback_used"
    assert payload["dispatch_status"] == "fallback_used"
    assert payload["fallback_used"] is True
    assert payload["external_message_id"] == "fallback-msg-1"
    _assert_no_raw_pii(result.message_text or "")
    _assert_no_raw_pii(payload)


def test_retryable_failure_records_retryable_queue_state(db_session):
    ticket = _ticket(db_session, case_type="failed_delivery")
    ctx = _case_context(ticket, issue_type="failed_delivery")
    _rule(db_session, issue_type="failed_delivery", destination_group_id=PROVIDER_GROUP_ID)

    class _Dispatcher:
        def send_group_message(self, *, provider_group_id: str, message: str, metadata: dict):
            return WhatsAppDispatchResult(ok=False, status="failed", error_code="timeout", retryable=True)

    result = route_ticket_to_whatsapp_group(db_session, ticket=ticket, case_context=ctx, dispatcher=_Dispatcher())

    assert result.routed is False
    assert result.status == OSROperationsDispatchStatus.RETRYABLE
    payload = _latest_event_payload(db_session, ticket)
    assert payload["dispatch_status"] == "retryable"
    assert payload["retryable"] is True
    _assert_no_raw_pii(payload)


def test_custom_template_only_renders_safe_allowed_fields(db_session):
    ticket = _ticket(db_session, case_type="address_issue")
    ctx = _case_context(ticket, issue_type="address_issue")
    _rule(
        db_session,
        issue_type="address_issue",
        destination_group_id=PROVIDER_GROUP_ID,
        message_template=(
            "Ticket {ticket_no} {safe_tracking_reference} {customer_claim_summary} "
            f"phone={RAW_PHONE} email={RAW_EMAIL} tracking={RAW_TRACKING} {RAW_ADDRESS} "
            "raw={raw_tracking_number}"
        ),
    )

    result = route_ticket_to_whatsapp_group(db_session, ticket=ticket, case_context=ctx)

    assert "[unavailable]" in (result.message_text or "")
    _assert_no_raw_pii(result.message_text or "")
    payload = _latest_event_payload(db_session, ticket)
    _assert_no_raw_pii(payload)
