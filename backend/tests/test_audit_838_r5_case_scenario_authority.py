from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/audit-838-r5-scenario.db",
)

from app.db import Base
from app.enums import SourceChannel, TicketPriority, TicketSource, TicketStatus
from app.model_registry import register_all_models
from app.models import Ticket
from app.models_case_scenario import CaseScenarioAssignment
from app.services.case_scenario_service import (
    current_case_scenario_assignment,
    load_runtime_scenario_catalog,
    reclassify_case_scenario,
    scenario_review_overdue,
)

register_all_models()


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'audit-838-r5-scenario.db'}",
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


def _ticket(
    suffix: str,
    *,
    case_type: str | None = None,
    sub_category: str | None = None,
    category: str | None = None,
    ai_classification: str | None = None,
) -> Ticket:
    return Ticket(
        ticket_no=f"R5-SCENARIO-{suffix}",
        title=f"Scenario {suffix}",
        description="Case Scenario Authority proof",
        source=TicketSource.manual,
        source_channel=SourceChannel.internal,
        priority=TicketPriority.medium,
        status=TicketStatus.new,
        case_type=case_type,
        sub_category=sub_category,
        category=category,
        ai_classification=ai_classification,
    )


def test_ticket_insert_pins_one_versioned_scenario_contract(db_session):
    ticket = _ticket(
        "PIN",
        case_type="delivery",
        sub_category="delivery_delay",
    )
    db_session.add(ticket)
    db_session.flush()

    row = current_case_scenario_assignment(db_session, ticket_id=ticket.id)
    assert row is not None
    assert row.scenario_key == "delivery_eta_delay_inquiry"
    assert len(row.catalog_sha256) == 64
    snapshot = json.loads(row.scenario_snapshot_json)
    assert snapshot["schema"] == "nexus.case-scenario-assignment.v1"
    assert snapshot["scenario"]["scenario_key"] == row.scenario_key
    assert snapshot["scenario"]["observation_period_seconds"] == 86400


def test_review_due_is_governance_warning_not_runtime_expiry():
    future = datetime(2028, 1, 1, tzinfo=timezone.utc)
    catalog = load_runtime_scenario_catalog(at=future)
    scenario = catalog.by_key()["tracking_status_inquiry"]
    assert scenario_review_overdue(scenario, at=future) is True
    assert scenario.lifecycle.expires_at is None


def test_conflicting_legacy_aliases_fail_closed_on_insert(db_session):
    ticket = _ticket(
        "CONFLICT",
        case_type="delivery_delay",
        sub_category="refund_request",
    )
    db_session.add(ticket)
    with pytest.raises(HTTPException) as exc:
        db_session.flush()
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "case_scenario_identity_conflict"


def test_ticket_load_does_not_rewrite_legacy_identity(db_session):
    ticket = _ticket("NO-PROJECTION", case_type="delivery_delay")
    db_session.add(ticket)
    db_session.commit()
    ticket_id = ticket.id

    db_session.expunge_all()
    loaded = db_session.get(Ticket, ticket_id)
    assert loaded is not None
    assert loaded.case_type == "delivery_delay"
    assignment = current_case_scenario_assignment(db_session, ticket_id=ticket_id)
    assert assignment is not None
    assert assignment.scenario_key == "delivery_eta_delay_inquiry"


def test_generic_field_edit_cannot_silently_reclassify_case(db_session):
    ticket = _ticket("GUARD", case_type="delivery_delay")
    db_session.add(ticket)
    db_session.flush()
    current = current_case_scenario_assignment(db_session, ticket_id=ticket.id)
    assert current is not None

    ticket.ai_classification = "refund_request"
    with pytest.raises(HTTPException) as exc:
        db_session.flush()
    assert exc.value.status_code == 409
    assert (
        exc.value.detail["code"]
        == "case_scenario_reclassification_command_required"
    )


def test_explicit_reclassification_preserves_history_and_snapshot(db_session):
    ticket = _ticket("RECLASSIFY", case_type="delivery_delay")
    db_session.add(ticket)
    db_session.flush()
    old = current_case_scenario_assignment(db_session, ticket_id=ticket.id)
    assert old is not None

    new = reclassify_case_scenario(
        db_session,
        ticket=ticket,
        scenario_key="formal_complaint",
        reason="Customer submitted a formal complaint after review.",
        actor_id=None,
    )
    db_session.flush()

    assert new.scenario_key == "formal_complaint"
    assert old.superseded_at is not None
    assert old.superseded_by_id == new.id
    assert current_case_scenario_assignment(
        db_session,
        ticket_id=ticket.id,
    ).id == new.id
    assert (
        db_session.query(CaseScenarioAssignment)
        .filter(CaseScenarioAssignment.ticket_id == ticket.id)
        .count()
        == 2
    )
    assert ticket.case_type == "delivery_delay"


def test_assignment_contract_columns_are_immutable(db_session):
    ticket = _ticket("IMMUTABLE", case_type="delivery_delay")
    db_session.add(ticket)
    db_session.flush()
    row = current_case_scenario_assignment(db_session, ticket_id=ticket.id)
    assert row is not None

    row.scenario_key = "formal_complaint"
    with pytest.raises(ValueError, match="case_scenario_assignment_immutable"):
        db_session.flush()
