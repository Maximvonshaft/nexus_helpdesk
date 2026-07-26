from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/structured_closure_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.db import Base  # noqa: E402
from app.enums import EventType, SourceChannel, TicketPriority, TicketSource  # noqa: E402
from app.models import Ticket, TicketEvent  # noqa: E402
from app.services.case_outcome_service import (  # noqa: E402
    append_case_outcome,
    record_case_evidence,
)
from app.services.ticket_closure_readiness import build_closure_snapshot  # noqa: E402
from app.utils.time import utc_now  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'closure.db'}",
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


def make_ticket(db) -> Ticket:
    row = Ticket(
        ticket_no="STRUCTURED-CLOSURE-1",
        title="Tracking status inquiry",
        description="Where is the parcel?",
        source=TicketSource.manual,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        case_type="tracking_status_inquiry",
        tracking_number="SAFE-REFERENCE",
    )
    db.add(row)
    db.flush()
    return row


def test_legacy_ticket_event_cannot_fabricate_closure(db_session):
    ticket = make_ticket(db_session)
    db_session.add(
        TicketEvent(
            ticket_id=ticket.id,
            actor_id=None,
            event_type=EventType.field_updated,
            field_name="closure_evidence",
            new_value="outcome:business_result_confirmed:completed",
            payload_json=json.dumps(
                {
                    "schema": "nexus.ticket-closure-evidence.v1",
                    "kind": "outcome",
                    "key": "business_result_confirmed",
                    "state": "completed",
                    "source_kind": "provider_receipt",
                    "source_ref": "legacy-only",
                    "source_revision": "v1",
                }
            ),
            created_at=utc_now(),
        )
    )
    db_session.flush()

    snapshot = build_closure_snapshot(db_session, ticket)

    assert snapshot.receipt["schema"] == "nexus.ticket-closure-receipt.v2"
    assert snapshot.readiness.closure_ready is False
    assert "business_result_confirmed" in snapshot.readiness.missing_outcome_levels
    assert snapshot.receipt["evidence"]["case_evidence_record_ids"] == []
    assert snapshot.receipt["evidence"]["case_outcome_record_ids"] == []


def test_structured_case_ledgers_can_satisfy_tracking_closure(db_session):
    ticket = make_ticket(db_session)
    now = utc_now()
    record_case_evidence(
        db_session,
        ticket_id=ticket.id,
        evidence_kind="fact",
        evidence_key="tracking_current_status",
        state="verified",
        source_kind="tracking",
        source_ref="tracking-receipt-1",
        source_revision="v1",
        observed_at=now,
        recorded_by=None,
    )
    append_case_outcome(
        db_session,
        ticket_id=ticket.id,
        record_type="execution_attempt",
        state="succeeded",
        idempotency_key="tracking-attempt-1",
        occurred_at=now,
        created_by=None,
        source_kind="tracking",
        source_id="tracking-receipt-1",
        payload={"action_class": "tracking_lookup"},
    )
    append_case_outcome(
        db_session,
        ticket_id=ticket.id,
        record_type="operational_outcome",
        state="confirmed",
        idempotency_key="tracking-outcome-1",
        occurred_at=now,
        created_by=None,
        source_kind="tracking",
        source_id="tracking-receipt-1",
        payload={"outcome_level": "business_result_confirmed"},
    )
    append_case_outcome(
        db_session,
        ticket_id=ticket.id,
        record_type="customer_notification",
        state="delivered",
        idempotency_key="tracking-notification-1",
        occurred_at=now,
        created_by=None,
        source_kind="customer_notification",
        source_id="message-1",
        payload={"notification_state": "delivered"},
    )

    snapshot = build_closure_snapshot(db_session, ticket)

    assert snapshot.readiness.closure_ready is True
    assert snapshot.receipt["readiness"]["blocked_reasons"] == []
    assert len(snapshot.receipt["evidence"]["case_evidence_record_ids"]) == 1
    assert len(snapshot.receipt["evidence"]["case_outcome_record_ids"]) == 3
    assert "ticket_event_ids" not in snapshot.receipt["evidence"]
    assert snapshot.receipt["evidence"]["contains_payloads"] is False
