from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, update
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/audit-838-r5-assignment-closure.db",
)

from app.db import Base
from app.enums import SourceChannel, TicketPriority, TicketSource, TicketStatus
from app.model_registry import register_all_models
from app.models import Ticket
from app.models_case_scenario import CaseScenarioAssignment
from app.services.case_scenario_service import current_case_scenario_assignment
from app.services.ticket_closure_readiness import build_closure_snapshot

register_all_models()


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'assignment-closure.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
    Base.metadata.create_all(engine)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _ticket(suffix: str, *, case_type: str | None) -> Ticket:
    return Ticket(
        ticket_no=f"R5-CLOSURE-{suffix}",
        title=f"Closure {suffix}",
        description="Assignment-bound closure proof",
        source=TicketSource.manual,
        source_channel=SourceChannel.internal,
        priority=TicketPriority.medium,
        status=TicketStatus.new,
        case_type=case_type,
        tracking_number=f"TRACK-{suffix}",
        customer_request="Where is my parcel?",
    )


def test_closure_consumes_assignment_snapshot_after_review_due(db_session):
    ticket = _ticket("REVIEW", case_type="tracking_inquiry")
    db_session.add(ticket)
    db_session.flush()
    assignment = current_case_scenario_assignment(
        db_session,
        ticket_id=ticket.id,
    )
    assert assignment is not None

    snapshot = build_closure_snapshot(
        db_session,
        ticket,
        now=datetime(2028, 1, 1, tzinfo=timezone.utc),
    )

    assert snapshot.receipt["scenario_assignment_id"] == assignment.id
    assert snapshot.receipt["scenario_key"] == "tracking_status_inquiry"
    assert snapshot.receipt["scenario_review_overdue"] is True
    assert "scenario_not_active" not in snapshot.readiness.blocked_reasons
    assert "scenario_catalog_contains_inactive_definition" not in (
        snapshot.readiness.blocked_reasons
    )


def test_mutable_ticket_classification_cannot_change_closure_contract(db_session):
    ticket = _ticket("PINNED", case_type="tracking_inquiry")
    db_session.add(ticket)
    db_session.flush()
    assignment = current_case_scenario_assignment(
        db_session,
        ticket_id=ticket.id,
    )
    assert assignment is not None

    db_session.execute(
        update(Ticket)
        .where(Ticket.id == ticket.id)
        .values(case_type="refund_request")
    )
    db_session.expire(ticket)

    snapshot = build_closure_snapshot(db_session, ticket)
    assert snapshot.receipt["scenario_assignment_id"] == assignment.id
    assert snapshot.receipt["scenario_key"] == "tracking_status_inquiry"


def test_missing_assignment_blocks_closure(db_session):
    ticket = _ticket("MISSING", case_type=None)
    db_session.add(ticket)
    db_session.flush()

    snapshot = build_closure_snapshot(db_session, ticket)
    assert snapshot.readiness.closure_ready is False
    assert snapshot.readiness.blocked_reasons == (
        "case_scenario_assignment_missing",
    )
    assert snapshot.receipt["scenario_assignment_id"] is None


def test_corrupt_assignment_snapshot_fails_closed(db_session):
    ticket = _ticket("CORRUPT", case_type="tracking_inquiry")
    db_session.add(ticket)
    db_session.flush()
    assignment = current_case_scenario_assignment(
        db_session,
        ticket_id=ticket.id,
    )
    assert assignment is not None
    assignment_id = assignment.id
    ticket_id = ticket.id

    db_session.execute(
        update(CaseScenarioAssignment)
        .where(CaseScenarioAssignment.id == assignment_id)
        .values(scenario_snapshot_json="{")
    )
    db_session.expire_all()

    ticket = db_session.get(Ticket, ticket_id)
    snapshot = build_closure_snapshot(db_session, ticket)
    assert snapshot.readiness.closure_ready is False
    assert snapshot.readiness.blocked_reasons == (
        "case_scenario_snapshot_invalid",
    )
    assert snapshot.receipt["scenario_assignment_id"] == assignment_id
