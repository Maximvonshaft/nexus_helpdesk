from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/speedaf_work_order_job_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.db import Base  # noqa: E402
from app.enums import ConversationState, EventType, SourceChannel, TicketPriority, TicketSource, TicketStatus  # noqa: E402
from app.models import BackgroundJob, Customer, Ticket, TicketEvent  # noqa: E402
from app.services import background_jobs  # noqa: E402
from app.services.speedaf.schemas import SpeedafWorkOrderResult  # noqa: E402
from app.tool_models import ToolCallLog, ToolRegistry  # noqa: F401,E402
from app import webchat_models  # noqa: F401,E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "speedaf_work_order_job.db"
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


def make_ticket(db_session) -> Ticket:
    customer = Customer(name="Speedaf Test Customer", phone="41000000000", external_ref="speedaf-test-customer")
    db_session.add(customer)
    db_session.flush()
    ticket = Ticket(
        ticket_no="T-SPD-1",
        title="WebChat handoff · SPX123456789CH",
        description="AI handoff snapshot",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        status=TicketStatus.pending_assignment,
        conversation_state=ConversationState.human_review_required,
        tracking_number="SPX123456789CH",
        case_type="delivery_reschedule",
        customer_request="Please urge delivery for my parcel",
    )
    db_session.add(ticket)
    db_session.flush()
    return ticket


def test_enqueue_speedaf_work_order_job_dedupes_by_ticket_and_type(db_session):
    ticket = make_ticket(db_session)

    first = background_jobs.enqueue_speedaf_work_order_create_job(
        db_session,
        ticket_id=ticket.id,
        conversation_id=101,
        waybill_code="SPX123456789CH",
        caller_id="41000000000",
        description="Please urge delivery",
    )
    second = background_jobs.enqueue_speedaf_work_order_create_job(
        db_session,
        ticket_id=ticket.id,
        conversation_id=101,
        waybill_code="SPX123456789CH",
        caller_id="41000000000",
        description="Please urge delivery again",
    )

    assert first.id == second.id
    assert db_session.query(BackgroundJob).filter_by(job_type=background_jobs.SPEEDAF_WORK_ORDER_CREATE_JOB).count() == 1
    payload = json.loads(first.payload_json)
    assert payload["workOrderType"] == "WT0103-05"
    assert payload["waybillCode"] == "SPX123456789CH"


def test_process_speedaf_work_order_job_writes_ticket_event_and_marks_done(db_session, monkeypatch):
    ticket = make_ticket(db_session)
    job = background_jobs.enqueue_speedaf_work_order_create_job(
        db_session,
        ticket_id=ticket.id,
        conversation_id=202,
        waybill_code="SPX123456789CH",
        caller_id="41000000000",
        description="Please urge delivery",
    )

    calls = []

    class FakeSpeedafActionService:
        def __init__(self, **kwargs):
            calls.append({"init": kwargs})

        def create_work_order(self, **kwargs):
            calls.append({"create_work_order": kwargs})
            return SpeedafWorkOrderResult(
                ok=True,
                status="created",
                external_id="WO-123",
                safe_payload={"request": {"waybill_suffix": "9CH"}, "response": {"ok": True}},
            )

    import app.services.speedaf.action_service as action_service

    monkeypatch.setattr(action_service, "SpeedafActionService", FakeSpeedafActionService)

    background_jobs.process_background_job(db_session, job)
    db_session.flush()

    assert job.status == TicketStatus.done or str(job.status.value if hasattr(job.status, "value") else job.status) == "done"
    event = db_session.query(TicketEvent).filter_by(ticket_id=ticket.id, field_name="speedaf_work_order").one()
    assert event.event_type == EventType.field_updated
    assert "completed" in event.note
    payload = json.loads(event.payload_json)
    assert payload["ok"] is True
    assert payload["external_id"] == "WO-123"
    assert payload["conversation_id"] == 202
    assert calls[0]["init"]["ticket_id"] == ticket.id
    assert calls[0]["init"]["webchat_conversation_id"] == 202
    assert calls[0]["init"]["background_job_id"] == job.id


def test_disabled_speedaf_work_order_job_records_skip_and_does_not_retry(db_session, monkeypatch):
    ticket = make_ticket(db_session)
    job = background_jobs.enqueue_speedaf_work_order_create_job(
        db_session,
        ticket_id=ticket.id,
        waybill_code="SPX123456789CH",
        caller_id="41000000000",
        description="Please urge delivery",
    )

    class FakeDisabledService:
        def __init__(self, **kwargs):
            pass

        def create_work_order(self, **kwargs):
            from app.services.speedaf.action_service import SpeedafActionDisabled

            raise SpeedafActionDisabled("speedaf_work_order_create_disabled")

    import app.services.speedaf.action_service as action_service

    monkeypatch.setattr(action_service, "SpeedafActionService", FakeDisabledService)

    background_jobs.process_background_job(db_session, job)
    db_session.flush()

    status = job.status.value if hasattr(job.status, "value") else str(job.status)
    assert status == "done"
    assert job.attempt_count == 0
    event = db_session.query(TicketEvent).filter_by(ticket_id=ticket.id, field_name="speedaf_work_order").one()
    payload = json.loads(event.payload_json)
    assert payload["status"] == "disabled"
    assert "skipped" in event.note
