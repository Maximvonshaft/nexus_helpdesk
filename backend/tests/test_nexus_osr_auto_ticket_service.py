from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/nexus_osr_auto_ticket_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

from app.db import Base
from app.enums import (
    ConversationState,
    EventType,
    SourceChannel,
    TicketPriority,
    TicketSource,
    TicketStatus,
)
from app.model_registry import register_all_models
from app.models import Customer, Ticket, TicketEvent
from app.models_osr import CaseContextRecord
from app.operator_models import OperatorTask
from app.services import operator_queue
from app.services.nexus_osr import auto_ticket_service
from app.services.nexus_osr.auto_ticket_service import (
    AutoTicketIdentityConflictError,
    AutoTicketReferencedTicketNotFoundError,
    create_or_reuse_ticket_from_case_context,
)
from app.services.nexus_osr.case_context import CaseContext, CaseContextStatus
from app.services.nexus_osr.persistence import load_case_context, save_case_context
from app.webchat_models import WebchatConversation

register_all_models()

RAW_EMAIL = "visitor@example.test"
RAW_PHONE = "+382 67123456"
RAW_TRACKING = "CH1234567890"


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "nexus_osr_auto_ticket.db"
    engine = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        future=True,
        expire_on_commit=False,
    )
    Base.metadata.create_all(engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _conversation(db_session, *, suffix: str, ticket_id: int | None = None) -> WebchatConversation:
    row = WebchatConversation(
        public_id=f"auto_ticket_{suffix}",
        visitor_token_hash=f"token-{suffix}",
        tenant_key="tenant-me",
        channel_key="webchat",
        ticket_id=ticket_id,
        visitor_name="Auto Ticket Visitor",
        status="open",
    )
    db_session.add(row)
    db_session.flush()
    return row


def _seed_ticket(
    db_session,
    *,
    suffix: str,
    status: TicketStatus = TicketStatus.pending_assignment,
    conversation_state: ConversationState = ConversationState.human_review_required,
    priority: TicketPriority = TicketPriority.medium,
    assignee_id: int | None = None,
) -> Ticket:
    customer = Customer(name=f"Customer {suffix}", external_ref=f"customer-{suffix}")
    db_session.add(customer)
    db_session.flush()
    ticket = Ticket(
        ticket_no=f"EXISTING-{suffix}",
        title="Existing ticket",
        description="Existing ticket",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.email,
        priority=priority,
        status=status,
        conversation_state=conversation_state,
        assignee_id=assignee_id,
        preferred_reply_channel="email",
        preferred_reply_contact="email:[redacted_email]",
        country_code="ME",
        case_type="original_case",
        customer_request="original request",
        required_action="Preserve this existing operator action",
    )
    db_session.add(ticket)
    db_session.flush()
    return ticket


def _event_payload(event: TicketEvent) -> dict:
    return json.loads(event.payload_json or "{}")


def test_create_is_atomic_and_transitions_case_context_identity_without_stale_active_row(db_session):
    customer = Customer(name="Auto Ticket Visitor", external_ref="auto-ticket-visitor")
    db_session.add(customer)
    conversation = _conversation(db_session, suffix="create")
    ctx = CaseContext(
        conversation_id=conversation.id,
        channel="webchat",
        country_code="ME",
        issue_type="signed_not_received",
        last_mcp_fact={
            "provider_payload": {
                "email": RAW_EMAIL,
                "phone": RAW_PHONE,
                "tracking": RAW_TRACKING,
            }
        },
    ).with_inbound_message(
        f"I did not receive {RAW_TRACKING}; email {RAW_EMAIL}; phone {RAW_PHONE}"
    ).with_contact_method(
        channel="whatsapp",
        value=RAW_PHONE,
        source="webchat_form",
    )
    save_case_context(db_session, ctx, tenant_id="tenant-me")
    db_session.flush()

    result = create_or_reuse_ticket_from_case_context(
        db_session,
        case_context=ctx,
        customer=customer,
        conversation=conversation,
        source_channel=SourceChannel.web_chat,
    )
    db_session.commit()

    assert result.created is True
    assert re.fullmatch(r"OSR-ME-\d{14}-[A-F0-9]{10}", result.ticket.ticket_no)
    assert len(result.ticket.ticket_no) <= 40
    assert result.ticket.case_type == "signed_not_received"
    assert result.ticket.customer_id == customer.id
    assert result.case_context.ticket_created is True
    assert conversation.ticket_id == result.ticket.id

    rows = (
        db_session.query(CaseContextRecord)
        .filter(
            CaseContextRecord.tenant_id == "tenant-me",
            CaseContextRecord.conversation_id == conversation.id,
        )
        .order_by(CaseContextRecord.id)
        .all()
    )
    assert len(rows) == 2
    assert rows[0].ticket_id is None
    assert rows[0].is_active is False
    assert rows[0].status == CaseContextStatus.CLOSED.value
    assert rows[1].ticket_id == result.ticket.id
    assert rows[1].is_active is True
    assert rows[1].status == CaseContextStatus.TICKET_CREATED.value

    loaded = load_case_context(
        db_session,
        conversation_id=conversation.id,
        tenant_id="tenant-me",
    )
    assert loaded is not None
    assert loaded.ticket_id == result.ticket.id
    assert loaded.ticket_created is True

    events = db_session.query(TicketEvent).filter(TicketEvent.ticket_id == result.ticket.id).all()
    assert len(events) == 1
    payload = _event_payload(events[0])
    encoded = events[0].payload_json or ""
    for raw in (RAW_EMAIL, RAW_PHONE, RAW_TRACKING):
        assert raw not in encoded
    assert "tracking_number_hash" not in encoded
    assert "last_mcp_fact" not in encoded
    assert "customer_claim_summary" not in encoded
    assert payload["operator_projection"] == "human_review_required"
    assert payload["case_context_state"]["has_tracking_reference"] is True
    assert payload["case_context_state"]["has_contact_method"] is True


def test_reused_closed_unassigned_ticket_reopens_without_overwriting_business_fields(db_session):
    ticket = _seed_ticket(
        db_session,
        suffix="closed-unassigned",
        status=TicketStatus.closed,
        conversation_state=ConversationState.replied_to_customer,
        priority=TicketPriority.urgent,
    )
    ticket.team_id = 777
    ticket.closed_at = auto_ticket_service.utc_now()
    ticket.resolved_at = auto_ticket_service.utc_now()
    original_reopen_count = ticket.reopen_count or 0
    conversation = _conversation(db_session, suffix="reuse-closed", ticket_id=ticket.id)

    reused = create_or_reuse_ticket_from_case_context(
        db_session,
        case_context=CaseContext(
            conversation_id=conversation.id,
            channel="webchat",
            country_code="US",
            issue_type="refund",
            customer_claim_summary="new claim",
        ),
        conversation=conversation,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.high,
        issue_type="refund",
    )

    assert reused.created is False
    assert reused.ticket.id == ticket.id
    assert ticket.status == TicketStatus.pending_assignment
    assert ticket.conversation_state == ConversationState.human_review_required
    assert ticket.priority == TicketPriority.urgent
    assert ticket.source_channel == SourceChannel.email
    assert ticket.preferred_reply_channel == "email"
    assert ticket.preferred_reply_contact == "email:[redacted_email]"
    assert ticket.country_code == "ME"
    assert ticket.case_type == "original_case"
    assert ticket.customer_request == "original request"
    assert ticket.required_action == "Preserve this existing operator action"
    assert ticket.team_id == 777
    assert ticket.closed_at is None
    assert ticket.resolved_at is None
    assert ticket.reopen_count == original_reopen_count + 1

    event = (
        db_session.query(TicketEvent)
        .filter(
            TicketEvent.ticket_id == ticket.id,
            TicketEvent.note == "Nexus OSR ticket reused",
        )
        .one()
    )
    payload = _event_payload(event)
    assert event.event_type == EventType.field_updated
    assert payload["operator_projection"] == "human_review_required"
    assert "source_channel" not in payload.get("changed_fields", [])
    assert "preferred_reply_contact" not in payload.get("changed_fields", [])


def test_reused_terminal_assigned_ticket_preserves_owner_and_enters_human_owned_queue(db_session):
    ticket = _seed_ticket(
        db_session,
        suffix="closed-assigned",
        status=TicketStatus.closed,
        conversation_state=ConversationState.replied_to_customer,
        priority=TicketPriority.high,
        assignee_id=123,
    )
    ticket.team_id = 456
    ticket.closed_at = auto_ticket_service.utc_now()
    ticket.resolved_at = auto_ticket_service.utc_now()

    reused = create_or_reuse_ticket_from_case_context(
        db_session,
        case_context=CaseContext(
            ticket_id=ticket.id,
            channel="webchat",
            country_code="ME",
            issue_type="tracking",
        ),
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
    )

    assert reused.created is False
    assert ticket.status == TicketStatus.in_progress
    assert ticket.conversation_state == ConversationState.human_owned
    assert ticket.assignee_id == 123
    assert ticket.team_id == 456
    assert ticket.required_action == "Preserve this existing operator action"
    assert ticket.priority == TicketPriority.high
    assert ticket.closed_at is None
    assert ticket.resolved_at is None


def test_reuse_event_and_operator_projection_are_idempotent(db_session):
    ticket = _seed_ticket(
        db_session,
        suffix="projection",
        status=TicketStatus.closed,
        conversation_state=ConversationState.replied_to_customer,
        priority=TicketPriority.medium,
    )
    ticket.required_action = None
    conversation = _conversation(db_session, suffix="projection", ticket_id=ticket.id)
    context = CaseContext(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        channel="webchat",
        country_code="ME",
        issue_type="complaint",
    )

    for _ in range(2):
        create_or_reuse_ticket_from_case_context(
            db_session,
            case_context=context,
            conversation=conversation,
            source_channel=SourceChannel.web_chat,
            priority=TicketPriority.high,
        )

    assert (
        db_session.query(TicketEvent)
        .filter(
            TicketEvent.ticket_id == ticket.id,
            TicketEvent.note == "Nexus OSR ticket reused",
        )
        .count()
        == 1
    )
    first_projection = operator_queue.project_webchat_handoff_tasks(db_session)
    second_projection = operator_queue.project_webchat_handoff_tasks(db_session)
    assert first_projection.created == 1
    assert second_projection.created == 0
    assert second_projection.skipped_existing == 1
    assert db_session.query(OperatorTask).filter(OperatorTask.ticket_id == ticket.id).count() == 1


def test_ticket_number_collision_retries_and_keeps_session_usable(db_session, monkeypatch):
    existing = _seed_ticket(db_session, suffix="collision")
    db_session.commit()
    generated = [existing.ticket_no, "OSR-ME-20260710120000-ABCDEF1234"]

    monkeypatch.setattr(
        auto_ticket_service,
        "_generate_ticket_no",
        lambda case_context, *, attempt=0: generated.pop(0),
    )
    result = create_or_reuse_ticket_from_case_context(
        db_session,
        case_context=CaseContext(channel="webchat", country_code="ME", issue_type="refund"),
        source_channel=SourceChannel.web_chat,
    )
    db_session.commit()

    assert result.created is True
    assert result.ticket.ticket_no == "OSR-ME-20260710120000-ABCDEF1234"
    assert db_session.query(Ticket).filter(Ticket.ticket_no == existing.ticket_no).count() == 1
    assert not db_session.new
    assert db_session.query(Ticket).count() == 2


def test_exhausted_collisions_roll_back_service_customer_and_allow_same_session_retry(db_session, monkeypatch):
    existing = _seed_ticket(db_session, suffix="exhausted")
    db_session.commit()
    before_customers = db_session.query(Customer).count()
    before_tickets = db_session.query(Ticket).count()

    monkeypatch.setattr(
        auto_ticket_service,
        "_generate_ticket_no",
        lambda case_context, *, attempt=0: existing.ticket_no,
    )
    with pytest.raises(IntegrityError):
        create_or_reuse_ticket_from_case_context(
            db_session,
            case_context=CaseContext(channel="webchat", country_code="ME", issue_type="refund"),
            source_channel=SourceChannel.web_chat,
        )

    assert db_session.query(Customer).count() == before_customers
    assert db_session.query(Ticket).count() == before_tickets

    monkeypatch.setattr(
        auto_ticket_service,
        "_generate_ticket_no",
        lambda case_context, *, attempt=0: "OSR-ME-20260710120001-ABCDEF1234",
    )
    recovered = create_or_reuse_ticket_from_case_context(
        db_session,
        case_context=CaseContext(channel="webchat", country_code="ME", issue_type="refund"),
        source_channel=SourceChannel.web_chat,
    )
    assert recovered.created is True
    assert db_session.query(Ticket).count() == before_tickets + 1


def test_event_failure_rolls_back_customer_ticket_context_transition_and_conversation(db_session, monkeypatch):
    conversation = _conversation(db_session, suffix="atomic-failure")
    context = CaseContext(
        conversation_id=conversation.id,
        channel="webchat",
        country_code="ME",
        issue_type="refund",
    )
    save_case_context(db_session, context, tenant_id="tenant-me")
    db_session.commit()
    before_customers = db_session.query(Customer).count()
    before_tickets = db_session.query(Ticket).count()

    monkeypatch.setattr(
        auto_ticket_service,
        "_write_ticket_event",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("event write failed")),
    )
    with pytest.raises(RuntimeError, match="event write failed"):
        create_or_reuse_ticket_from_case_context(
            db_session,
            case_context=context,
            conversation=conversation,
            source_channel=SourceChannel.web_chat,
        )

    db_session.refresh(conversation)
    assert conversation.ticket_id is None
    assert db_session.query(Customer).count() == before_customers
    assert db_session.query(Ticket).count() == before_tickets
    assert db_session.query(TicketEvent).count() == 0
    rows = (
        db_session.query(CaseContextRecord)
        .filter(
            CaseContextRecord.tenant_id == "tenant-me",
            CaseContextRecord.conversation_id == conversation.id,
        )
        .all()
    )
    assert len(rows) == 1
    assert rows[0].is_active is True
    assert rows[0].status == CaseContextStatus.ACTIVE.value
    assert rows[0].ticket_id is None

    monkeypatch.undo()
    recovered = create_or_reuse_ticket_from_case_context(
        db_session,
        case_context=context,
        conversation=conversation,
        source_channel=SourceChannel.web_chat,
    )
    assert recovered.created is True
    assert db_session.query(Ticket).count() == before_tickets + 1


def test_conflicting_conversation_and_case_context_ticket_ids_fail_closed(db_session):
    ticket_a = _seed_ticket(db_session, suffix="identity-a")
    ticket_b = _seed_ticket(db_session, suffix="identity-b")
    conversation = _conversation(db_session, suffix="identity-conflict", ticket_id=ticket_a.id)
    before_status = ticket_a.status

    with pytest.raises(AutoTicketIdentityConflictError, match="ticket_identity_conflict"):
        create_or_reuse_ticket_from_case_context(
            db_session,
            case_context=CaseContext(
                conversation_id=conversation.id,
                ticket_id=ticket_b.id,
                channel="webchat",
                country_code="ME",
            ),
            conversation=conversation,
        )

    assert conversation.ticket_id == ticket_a.id
    assert ticket_a.status == before_status
    assert db_session.query(TicketEvent).count() == 0


def test_conflicting_conversation_identity_and_missing_ticket_reference_fail_closed(db_session):
    conversation = _conversation(db_session, suffix="conversation-conflict")

    with pytest.raises(AutoTicketIdentityConflictError, match="conversation_identity_conflict"):
        create_or_reuse_ticket_from_case_context(
            db_session,
            case_context=CaseContext(
                conversation_id=conversation.id + 1,
                channel="webchat",
                country_code="ME",
            ),
            conversation=conversation,
        )

    with pytest.raises(AutoTicketReferencedTicketNotFoundError):
        create_or_reuse_ticket_from_case_context(
            db_session,
            case_context=CaseContext(
                ticket_id=999999,
                channel="webchat",
                country_code="ME",
            ),
        )
    assert db_session.query(TicketEvent).count() == 0


def test_postgres_unique_violation_uses_sqlstate_and_constraint_diagnostics():
    class Diag:
        constraint_name = "ix_tickets_ticket_no"

    class PostgresUniqueError(Exception):
        sqlstate = "23505"
        diag = Diag()

    exc = IntegrityError(
        "INSERT INTO tickets (ticket_no) VALUES (%(ticket_no)s)",
        {"ticket_no": "OSR-ME-20260710120000-ABCDEF1234"},
        PostgresUniqueError("duplicate key"),
    )
    assert auto_ticket_service._is_ticket_no_unique_violation(exc) is True

    class OtherDiag:
        constraint_name = "customers_external_ref_key"

    class OtherUniqueError(Exception):
        sqlstate = "23505"
        diag = OtherDiag()

    other = IntegrityError(
        "INSERT INTO customers (external_ref) VALUES (%(external_ref)s)",
        {"external_ref": "duplicate"},
        OtherUniqueError("duplicate key"),
    )
    assert auto_ticket_service._is_ticket_no_unique_violation(other) is False


def test_source_has_no_count_plus_one_and_no_full_case_context_event_payload():
    source = Path(auto_ticket_service.__file__).read_text(encoding="utf-8")
    assert "count() + 1" not in source
    assert "case_context.as_dict()" not in source
    assert "MAX_TICKET_NO_GENERATION_ATTEMPTS = 5" in source
    assert "with_for_update()" in source
    assert "close_case_context(" in source
