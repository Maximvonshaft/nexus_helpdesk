from __future__ import annotations

import json
import os
import re
import sys
import threading
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/nexus_osr_auto_ticket_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models, models_osr, operator_models, webchat_models  # noqa: F401,E402
from app.db import Base  # noqa: E402
from app.enums import ConversationState, EventType, SourceChannel, TicketPriority, TicketStatus  # noqa: E402
from app.models import Customer, Ticket, TicketEvent  # noqa: E402
from app.operator_models import OperatorTask  # noqa: E402
from app.services import operator_queue  # noqa: E402
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


def _event_payload(event: TicketEvent) -> dict:
    return json.loads(event.payload_json or "{}")


def test_auto_ticket_creates_ticket_and_case_context_with_safe_event(db_session):
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
    raw_email = "visitor@example.test"
    raw_phone = "+382 67123456"
    raw_tracking = "CH1234567890"
    ctx = CaseContext(
        conversation_id=conversation.id,
        channel="webchat",
        country_code="ME",
        issue_type="signed_not_received",
        last_mcp_fact={"provider_payload": {"email": raw_email, "phone": raw_phone, "tracking": raw_tracking}},
    ).with_inbound_message(
        f"I did not receive {raw_tracking}; email {raw_email}; phone {raw_phone}"
    ).with_contact_method(
        channel="whatsapp",
        value=raw_phone,
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
    assert re.fullmatch(r"OSR-ME-\d{14}-[A-F0-9]{10}", result.ticket.ticket_no)
    assert len(result.ticket.ticket_no) <= 40
    assert result.ticket.case_type == "signed_not_received"
    assert result.ticket.customer_id == customer.id
    assert result.case_context.ticket_created is True
    assert conversation.ticket_id == result.ticket.id
    events = db_session.query(TicketEvent).filter(TicketEvent.ticket_id == result.ticket.id).all()
    assert len(events) == 1
    encoded = events[0].payload_json or ""
    assert raw_email not in encoded
    assert raw_phone not in encoded
    assert raw_tracking not in encoded
    assert "tracking_number_hash" not in encoded
    assert "last_mcp_fact" not in encoded
    assert "customer_claim_summary" not in encoded
    loaded = load_case_context(db_session, conversation_id=conversation.id, ticket_id=result.ticket.id)
    assert loaded is not None
    assert loaded.ticket_created is True


def test_reused_closed_ticket_reopens_for_human_review_without_overwriting_business_fields(db_session):
    first = create_or_reuse_ticket_from_case_context(
        db_session,
        case_context=CaseContext(channel="email", country_code="ME", issue_type="tracking"),
        source_channel=SourceChannel.email,
        priority=TicketPriority.urgent,
    )
    first.ticket.status = TicketStatus.closed
    first.ticket.conversation_state = ConversationState.replied_to_customer
    first.ticket.priority = TicketPriority.urgent
    first.ticket.source_channel = SourceChannel.email
    first.ticket.preferred_reply_channel = "email"
    first.ticket.preferred_reply_contact = "email:[redacted_email]"
    first.ticket.country_code = "ME"
    first.ticket.case_type = "original_case"
    first.ticket.customer_request = "original request"
    first.ticket.required_action = "Preserve this existing operator action"
    first.ticket.team_id = 777
    first.ticket.closed_at = auto_ticket_service.utc_now()
    first.ticket.resolved_at = auto_ticket_service.utc_now()
    original_reopen_count = first.ticket.reopen_count or 0
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
    assert reused.ticket.id == first.ticket.id
    assert reused.ticket.status == TicketStatus.pending_assignment
    assert reused.ticket.conversation_state == ConversationState.human_review_required
    assert reused.ticket.priority == TicketPriority.urgent
    assert reused.ticket.source_channel == SourceChannel.email
    assert reused.ticket.preferred_reply_channel == "email"
    assert reused.ticket.preferred_reply_contact == "email:[redacted_email]"
    assert reused.ticket.country_code == "ME"
    assert reused.ticket.case_type == "original_case"
    assert reused.ticket.customer_request == "original request"
    assert reused.ticket.required_action == "Preserve this existing operator action"
    assert reused.ticket.team_id == 777
    assert reused.ticket.closed_at is None
    assert reused.ticket.resolved_at is None
    assert reused.ticket.reopen_count == original_reopen_count + 1

    reuse_event = (
        db_session.query(TicketEvent)
        .filter(TicketEvent.ticket_id == reused.ticket.id, TicketEvent.note == "Nexus OSR ticket reused")
        .one()
    )
    assert reuse_event.event_type == EventType.field_updated
    payload = _event_payload(reuse_event)
    assert payload["operator_projection"] == "human_review_required"
    assert "source_channel" not in payload.get("changed_fields", [])
    assert "preferred_reply_contact" not in payload.get("changed_fields", [])


def test_reused_assigned_ticket_preserves_owner_status_and_existing_action(db_session):
    first = create_or_reuse_ticket_from_case_context(
        db_session,
        case_context=CaseContext(channel="webchat", country_code="ME", issue_type="tracking"),
        source_channel=SourceChannel.web_chat,
    )
    first.ticket.status = TicketStatus.in_progress
    first.ticket.conversation_state = ConversationState.human_owned
    first.ticket.assignee_id = 123
    first.ticket.team_id = 456
    first.ticket.required_action = "Existing assigned work"
    first.ticket.priority = TicketPriority.high
    db_session.flush()

    reused = create_or_reuse_ticket_from_case_context(
        db_session,
        case_context=CaseContext(ticket_id=first.ticket.id, channel="webchat", country_code="ME"),
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
    )

    assert reused.ticket.status == TicketStatus.in_progress
    assert reused.ticket.assignee_id == 123
    assert reused.ticket.team_id == 456
    assert reused.ticket.required_action == "Existing assigned work"
    assert reused.ticket.priority == TicketPriority.high
    assert reused.ticket.conversation_state == ConversationState.human_owned


def test_reused_ticket_event_and_operator_projection_are_idempotent(db_session):
    first = create_or_reuse_ticket_from_case_context(
        db_session,
        case_context=CaseContext(channel="webchat", country_code="ME", issue_type="tracking"),
        source_channel=SourceChannel.web_chat,
    )
    first.ticket.status = TicketStatus.closed
    first.ticket.conversation_state = ConversationState.replied_to_customer
    first.ticket.required_action = None
    conversation = WebchatConversation(
        public_id="auto_ticket_wc_projection",
        visitor_token_hash="token-hash-projection",
        tenant_key="pytest",
        channel_key="webchat",
        ticket_id=first.ticket.id,
        visitor_name="Auto Ticket Visitor",
        status="open",
    )
    db_session.add(conversation)
    db_session.flush()
    context = CaseContext(
        conversation_id=conversation.id,
        ticket_id=first.ticket.id,
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
        .filter(TicketEvent.ticket_id == first.ticket.id, TicketEvent.note == "Nexus OSR ticket reused")
        .count()
        == 1
    )
    first_projection = operator_queue.project_webchat_handoff_tasks(db_session)
    second_projection = operator_queue.project_webchat_handoff_tasks(db_session)
    assert first_projection.created == 1
    assert second_projection.created == 0
    assert second_projection.skipped_existing == 1
    assert db_session.query(OperatorTask).filter(OperatorTask.ticket_id == first.ticket.id).count() == 1


def test_auto_ticket_retries_ticket_no_unique_collision_and_keeps_session_usable(db_session, monkeypatch):
    first = create_or_reuse_ticket_from_case_context(
        db_session,
        case_context=CaseContext(channel="webchat", country_code="ME", issue_type="tracking"),
        source_channel=SourceChannel.web_chat,
    )
    db_session.commit()
    generated = [first.ticket.ticket_no, "OSR-ME-20260710120000-ABCDEF1234"]

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
    assert result.ticket.ticket_no == "OSR-ME-20260710120000-ABCDEF1234"
    assert db_session.query(Ticket).filter(Ticket.ticket_no == first.ticket.ticket_no).count() == 1
    assert not db_session.new
    assert db_session.query(Ticket).count() == 2


def test_exhausted_collisions_roll_back_service_customer_and_allow_same_session_retry(db_session, monkeypatch):
    first = create_or_reuse_ticket_from_case_context(
        db_session,
        case_context=CaseContext(channel="webchat", country_code="ME", issue_type="tracking"),
        source_channel=SourceChannel.web_chat,
    )
    db_session.commit()
    before_customers = db_session.query(Customer).count()
    before_tickets = db_session.query(Ticket).count()

    monkeypatch.setattr(
        auto_ticket_service,
        "_generate_ticket_no",
        lambda case_context, *, attempt=0: first.ticket.ticket_no,
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


def test_ticket_event_failure_rolls_back_customer_ticket_context_and_conversation_atomically(db_session, monkeypatch):
    conversation = WebchatConversation(
        public_id="auto_ticket_atomic_failure",
        visitor_token_hash="token-hash-atomic-failure",
        tenant_key="pytest",
        channel_key="webchat",
        visitor_name="Atomic Failure Visitor",
        status="open",
    )
    db_session.add(conversation)
    db_session.commit()
    before_customers = db_session.query(Customer).count()
    before_tickets = db_session.query(Ticket).count()

    def fail_event(*_args, **_kwargs):
        raise RuntimeError("event write failed")

    monkeypatch.setattr(auto_ticket_service, "_write_ticket_event", fail_event)
    with pytest.raises(RuntimeError, match="event write failed"):
        create_or_reuse_ticket_from_case_context(
            db_session,
            case_context=CaseContext(
                conversation_id=conversation.id,
                channel="webchat",
                country_code="ME",
                issue_type="refund",
            ),
            conversation=conversation,
            source_channel=SourceChannel.web_chat,
        )

    db_session.refresh(conversation)
    assert conversation.ticket_id is None
    assert db_session.query(Customer).count() == before_customers
    assert db_session.query(Ticket).count() == before_tickets
    assert db_session.query(TicketEvent).count() == 0
    assert load_case_context(db_session, conversation_id=conversation.id) is None

    monkeypatch.undo()
    recovered = create_or_reuse_ticket_from_case_context(
        db_session,
        case_context=CaseContext(
            conversation_id=conversation.id,
            channel="webchat",
            country_code="ME",
            issue_type="refund",
        ),
        conversation=conversation,
        source_channel=SourceChannel.web_chat,
    )
    assert recovered.created is True
    assert db_session.query(Ticket).count() == before_tickets + 1


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


_POSTGRES_TEST_URL = os.getenv("NEXUS_OSR_POSTGRES_TEST_URL")


@pytest.mark.skipif(not _POSTGRES_TEST_URL, reason="Set NEXUS_OSR_POSTGRES_TEST_URL for the PostgreSQL concurrency probe")
def test_postgres_concurrent_unique_collision_recovers_inside_savepoint():
    """Repeatable PostgreSQL probe for SQLSTATE 23505 + savepoint reuse.

    Run with an isolated PostgreSQL database, for example:
    NEXUS_OSR_POSTGRES_TEST_URL=postgresql+psycopg://helpdesk@127.0.0.1:5432/helpdesk \
      pytest -q backend/tests/test_nexus_osr_auto_ticket_service.py -k postgres_concurrent
    """

    engine = create_engine(_POSTGRES_TEST_URL, future=True, pool_pre_ping=True)
    table_name = f"osr_ticket_no_probe_{uuid.uuid4().hex[:12]}"
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    common = "OSR-CONCURRENT-COLLISION"
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    with engine.begin() as conn:
        conn.exec_driver_sql(f'CREATE TABLE "{table_name}" (ticket_no varchar(40) UNIQUE NOT NULL)')

    def worker(index: int) -> None:
        session = Session()
        try:
            with session.begin():
                barrier.wait(timeout=10)
                for attempt in range(2):
                    candidate = common if attempt == 0 else f"OSR-CONCURRENT-{index}-{uuid.uuid4().hex[:8]}"
                    try:
                        with session.begin_nested():
                            session.execute(
                                text(f'INSERT INTO "{table_name}" (ticket_no) VALUES (:ticket_no)'),
                                {"ticket_no": candidate},
                            )
                        with lock:
                            outcomes.append(candidate)
                        break
                    except IntegrityError as exc:
                        assert auto_ticket_service._is_ticket_no_unique_violation(exc)
                        session.execute(text(f'SELECT ticket_no FROM "{table_name}" WHERE ticket_no = :ticket_no'), {"ticket_no": candidate}).first()
                else:
                    raise AssertionError("worker did not recover from the unique collision")
        except BaseException as exc:  # pragma: no cover - only used by opt-in external probe
            with lock:
                errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=worker, args=(index,), daemon=True) for index in range(2)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        assert all(not thread.is_alive() for thread in threads)
        assert not errors
        assert len(outcomes) == 2
        assert len(set(outcomes)) == 2
        with engine.connect() as conn:
            assert conn.execute(text(f'SELECT count(*) FROM "{table_name}"')).scalar_one() == 2
    finally:
        with engine.begin() as conn:
            conn.exec_driver_sql(f'DROP TABLE IF EXISTS "{table_name}"')
        engine.dispose()
