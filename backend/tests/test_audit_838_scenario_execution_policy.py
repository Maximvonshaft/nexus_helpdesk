from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/audit-838-scenario-execution-policy.db",
)

from app.db import Base
from app.enums import SourceChannel, TicketPriority, TicketSource, TicketStatus
from app.model_registry import register_all_models
from app.models import Tenant, Ticket
from app.services.ticket_scenario_assignment_service import (
    TicketScenarioAssignmentError,
    assign_ticket_scenario,
    get_assigned_scenario,
    require_scenario_action_allowed,
)

register_all_models()


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'scenario-execution-policy.db'}",
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


def _ticket(db, *, scenario_key: str) -> Ticket:
    tenant = Tenant(
        tenant_key=f"audit-838-{scenario_key}",
        display_name="Audit 838 Scenario Tenant",
        is_active=True,
    )
    db.add(tenant)
    db.flush()
    ticket = Ticket(
        tenant_id=tenant.id,
        tenant_assignment_source="fixture",
        tenant_assignment_version="sha256:" + "a" * 64,
        ticket_no=f"AUD-838-{scenario_key}",
        title="Scenario execution policy",
        description="Frozen Scenario policy proof",
        source=TicketSource.manual,
        source_channel=SourceChannel.internal,
        priority=TicketPriority.medium,
        status=TicketStatus.pending_assignment,
        case_type=scenario_key,
    )
    db.add(ticket)
    db.flush()
    assign_ticket_scenario(
        db,
        ticket=ticket,
        scenario_key=scenario_key,
        actor_id=None,
        source="audit_test",
        reason="prove frozen execution policy",
        allow_reclassification=False,
    )
    db.flush()
    return ticket


def _definition_digest(definition: dict) -> str:
    canonical = json.dumps(
        definition,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_scenario_policy_allows_declared_action_and_blocks_undeclared_provider_write(
    db_session,
):
    ticket = _ticket(db_session, scenario_key="tracking_status_inquiry")

    policy = require_scenario_action_allowed(
        db_session,
        ticket=ticket,
        action_class="tracking_lookup",
    )
    assert policy.scenario_key == "tracking_status_inquiry"
    assert policy.action_is_allowed("tracking_lookup") is True

    with pytest.raises(HTTPException) as exc:
        require_scenario_action_allowed(
            db_session,
            ticket=ticket,
            action_class="create_delivery_work_order",
        )
    assert exc.value.status_code == 409
    assert exc.value.detail == "scenario_action_not_allowed"


def test_review_due_is_governance_warning_not_runtime_kill_switch(db_session):
    ticket = _ticket(db_session, scenario_key="delivery_followup_work_order")
    assigned = get_assigned_scenario(db_session, ticket=ticket, required=True)
    assert assigned is not None

    definition = dict(assigned.assignment.definition_json)
    lifecycle = dict(definition["lifecycle"])
    lifecycle["review_due"] = "2026-07-20T00:00:00+00:00"
    lifecycle["expires_at"] = None
    definition["lifecycle"] = lifecycle
    assigned.assignment.definition_json = definition
    assigned.assignment.definition_sha256 = _definition_digest(definition)
    db_session.flush()

    refreshed = get_assigned_scenario(db_session, ticket=ticket, required=True)
    assert refreshed is not None
    assert refreshed.policy.review_overdue is True
    assert refreshed.policy.action_is_allowed("create_delivery_work_order") is True


def test_explicit_scenario_expiry_fails_closed(db_session):
    ticket = _ticket(db_session, scenario_key="delivery_followup_work_order")
    assigned = get_assigned_scenario(db_session, ticket=ticket, required=True)
    assert assigned is not None

    definition = dict(assigned.assignment.definition_json)
    lifecycle = dict(definition["lifecycle"])
    lifecycle["expires_at"] = "2026-07-26T00:00:00+00:00"
    definition["lifecycle"] = lifecycle
    assigned.assignment.definition_json = definition
    assigned.assignment.definition_sha256 = _definition_digest(definition)
    db_session.flush()

    with pytest.raises(TicketScenarioAssignmentError) as exc:
        get_assigned_scenario(db_session, ticket=ticket, required=True)
    assert str(exc.value) == "scenario_not_operationally_active"


def test_speedaf_endpoints_map_to_catalog_action_classes():
    source = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "api"
        / "speedaf_actions.py"
    ).read_text(encoding="utf-8")
    for action_class in (
        'action_class="tracking_lookup"',
        'action_class="create_delivery_work_order"',
        'action_class="update_address_contact"',
    ):
        assert action_class in source
    assert "require_scenario_action_allowed" in source
