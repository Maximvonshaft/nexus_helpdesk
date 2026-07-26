from __future__ import annotations

import os
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/case_outcome_ledger_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.db import Base  # noqa: E402
from app.enums import SourceChannel, TicketPriority, TicketSource  # noqa: E402
from app.models import Ticket  # noqa: E402
from app.services.case_outcome_service import (  # noqa: E402
    append_case_outcome,
    project_case_ledger,
    record_case_evidence,
)
from app.utils.time import utc_now  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'ledger.db'}",
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
        ticket_no="CASE-LEDGER-1",
        title="Tracking inquiry",
        description="Where is my parcel?",
        source=TicketSource.manual,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        case_type="tracking_status_inquiry",
        tracking_number="SAFE-TRACKING-REFERENCE",
    )
    db.add(row)
    db.flush()
    return row


def test_case_evidence_is_idempotent_and_conflicting_rewrite_fails(db_session):
    ticket = make_ticket(db_session)
    observed = utc_now()

    first, created = record_case_evidence(
        db_session,
        ticket_id=ticket.id,
        evidence_kind="fact",
        evidence_key="tracking_current_status",
        state="verified",
        source_kind="tracking",
        source_ref="receipt-1",
        source_revision="v1",
        observed_at=observed,
        recorded_by=None,
        safe_metadata={"status_code": "out_for_delivery"},
    )
    same, created_again = record_case_evidence(
        db_session,
        ticket_id=ticket.id,
        evidence_kind="fact",
        evidence_key="tracking_current_status",
        state="verified",
        source_kind="tracking",
        source_ref="receipt-1",
        source_revision="v1",
        observed_at=observed,
        recorded_by=None,
        safe_metadata={"status_code": "out_for_delivery"},
    )

    assert created is True
    assert created_again is False
    assert same.id == first.id

    with pytest.raises(ValueError, match="case_evidence_idempotency_conflict"):
        record_case_evidence(
            db_session,
            ticket_id=ticket.id,
            evidence_kind="fact",
            evidence_key="tracking_current_status",
            state="failed",
            source_kind="tracking",
            source_ref="receipt-1",
            source_revision="v1",
            observed_at=observed,
            recorded_by=None,
            safe_metadata={"status_code": "different"},
        )


def test_outcome_ledger_sequences_records_and_redacts_sensitive_payload(db_session):
    ticket = make_ticket(db_session)
    now = utc_now()

    attempt, created = append_case_outcome(
        db_session,
        ticket_id=ticket.id,
        record_type="execution_attempt",
        state="succeeded",
        idempotency_key="tracking-attempt-1",
        occurred_at=now,
        created_by=None,
        source_kind="tool_execution",
        source_id="attempt-1",
        payload={
            "action_class": "tracking_lookup",
            "authorization": "Bearer DO_NOT_STORE",
            "provider_payload": {"customer_phone": "+411234567"},
        },
    )
    outcome, _ = append_case_outcome(
        db_session,
        ticket_id=ticket.id,
        record_type="operational_outcome",
        state="confirmed",
        idempotency_key="tracking-outcome-1",
        occurred_at=now + timedelta(seconds=1),
        created_by=None,
        parent_record_id=attempt.id,
        source_kind="tracking",
        source_id="receipt-1",
        payload={"outcome_level": "business_result_confirmed"},
    )

    assert created is True
    assert attempt.sequence == 1
    assert outcome.sequence == 2
    assert attempt.payload_json["authorization"]["redacted"] is True
    assert attempt.payload_json["provider_payload"]["redacted"] is True
    assert "DO_NOT_STORE" not in str(attempt.payload_json)
    assert "+411234567" not in str(attempt.payload_json)

    same, created_again = append_case_outcome(
        db_session,
        ticket_id=ticket.id,
        record_type="execution_attempt",
        state="succeeded",
        idempotency_key="tracking-attempt-1",
        occurred_at=now,
        created_by=None,
        source_kind="tool_execution",
        source_id="attempt-1",
        payload={
            "action_class": "tracking_lookup",
            "authorization": "Bearer DO_NOT_STORE",
            "provider_payload": {"customer_phone": "+411234567"},
        },
    )
    assert created_again is False
    assert same.id == attempt.id


def test_projection_uses_evidence_action_outcome_and_notification(db_session):
    ticket = make_ticket(db_session)
    now = utc_now()
    record_case_evidence(
        db_session,
        ticket_id=ticket.id,
        evidence_kind="fact",
        evidence_key="tracking_current_status",
        state="verified",
        source_kind="tracking",
        source_ref="receipt-1",
        source_revision="v1",
        observed_at=now,
        recorded_by=None,
    )
    append_case_outcome(
        db_session,
        ticket_id=ticket.id,
        record_type="execution_attempt",
        state="succeeded",
        idempotency_key="attempt-1",
        occurred_at=now,
        created_by=None,
        payload={"action_class": "tracking_lookup"},
    )
    append_case_outcome(
        db_session,
        ticket_id=ticket.id,
        record_type="operational_outcome",
        state="confirmed",
        idempotency_key="outcome-1",
        occurred_at=now,
        created_by=None,
        payload={"outcome_level": "business_result_confirmed"},
    )
    append_case_outcome(
        db_session,
        ticket_id=ticket.id,
        record_type="customer_notification",
        state="delivered",
        idempotency_key="notify-1",
        occurred_at=now,
        created_by=None,
        payload={"notification_state": "delivered"},
    )

    projection = project_case_ledger(db_session, ticket_id=ticket.id)

    assert projection.fact_classes == frozenset({"tracking_current_status"})
    assert projection.action_classes == frozenset({"tracking_lookup", "notify_customer"})
    assert projection.outcome_levels == frozenset(
        {"technical_completed", "business_result_confirmed", "customer_notified"}
    )
    assert projection.notification_state == "delivered"
    assert projection.repair_required is False
