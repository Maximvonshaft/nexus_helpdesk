from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/sla_policy_revision_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.db import Base  # noqa: E402
from app.enums import SourceChannel, TicketPriority, TicketSource  # noqa: E402
from app.models import Market, SLAPolicy, Tenant, Ticket  # noqa: E402
from app.models_case_governance import (  # noqa: E402
    SLAPolicyRevision,
    TicketSLAAssignment,
    TicketSLAPauseInterval,
)
from app.models_sla_runtime import TicketSLATarget  # noqa: E402
from app.services.sla_service import (  # noqa: E402
    SLAConfigurationError,
    add_business_minutes,
    apply_policy_to_ticket,
    business_seconds_between,
    select_policy_revision,
)
from app.services.ticket_sla_policy import sla_risk_filter  # noqa: E402
from app.utils.time import ensure_utc  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'sla.db'}",
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


def make_scope(db):
    tenant = Tenant(
        tenant_key="sla-tenant",
        display_name="SLA Tenant",
        is_active=True,
    )
    db.add(tenant)
    db.flush()
    market = Market(
        tenant_id=tenant.id,
        tenant_assignment_source="test",
        tenant_assignment_version="v1",
        code="CH-SLA",
        name="Switzerland SLA",
        country_code="CH",
        language_code="de",
        timezone="Europe/Zurich",
        is_active=True,
    )
    db.add(market)
    db.flush()
    return tenant, market


def make_policy(db, priority=TicketPriority.medium):
    policy = SLAPolicy(
        name=f"{priority.value}-policy",
        priority=priority,
        first_response_minutes=120,
        resolution_minutes=1440,
        pause_on_waiting_customer=True,
        pause_on_waiting_internal=False,
    )
    db.add(policy)
    db.flush()
    return policy


def make_ticket(db, tenant, market, *, priority=TicketPriority.medium):
    created = datetime(2026, 7, 31, 14, 0, tzinfo=timezone.utc)
    ticket = Ticket(
        tenant_id=tenant.id,
        tenant_assignment_source="test",
        tenant_assignment_version="v1",
        ticket_no=f"SLA-{priority.value}",
        title="SLA case",
        description="SLA case",
        source=TicketSource.manual,
        source_channel=SourceChannel.web_chat,
        priority=priority,
        market_id=market.id,
        country_code="CH",
        case_type="tracking_status_inquiry",
        created_at=created,
        updated_at=created,
    )
    db.add(ticket)
    db.flush()
    return ticket


def business_schedule():
    return {
        day: [{"start": "09:00", "end": "17:00"}]
        for day in (
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
        )
    }


def approved_revision(
    db,
    policy,
    tenant,
    market,
    *,
    version=1,
    first_minutes=120,
    resolution_minutes=480,
    risk_window=45,
    customer_tier=None,
):
    revision = SLAPolicyRevision(
        policy_id=policy.id,
        version=version,
        tenant_id=tenant.id,
        is_global_template=False,
        market_id=market.id,
        channel_key="web_chat",
        scenario_key="tracking_status_inquiry",
        customer_tier=customer_tier,
        status="approved",
        timezone_name="Europe/Zurich",
        weekly_schedule_json=business_schedule(),
        holidays_json=[],
        first_response_minutes=first_minutes,
        resolution_minutes=resolution_minutes,
        action_minutes=60,
        notification_minutes=30,
        risk_window_minutes=risk_window,
        pause_reasons_json=["waiting_customer"],
        effective_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
        effective_to=None,
        approved_by=None,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    db.add(revision)
    db.flush()
    return revision


def test_business_calendar_crosses_weekend_and_holiday():
    start = datetime(2026, 7, 31, 14, 0, tzinfo=timezone.utc)

    monday = add_business_minutes(
        start,
        120,
        timezone_name="Europe/Zurich",
        weekly_schedule=business_schedule(),
        holidays=[],
    )
    tuesday = add_business_minutes(
        start,
        120,
        timezone_name="Europe/Zurich",
        weekly_schedule=business_schedule(),
        holidays=["2026-08-03"],
    )

    assert monday == datetime(2026, 8, 3, 8, 0, tzinfo=timezone.utc)
    assert tuesday == datetime(2026, 8, 4, 8, 0, tzinfo=timezone.utc)


def test_business_seconds_between_excludes_nights_and_weekend():
    start = datetime(2026, 7, 31, 14, 30, tzinfo=timezone.utc)
    end = datetime(2026, 8, 3, 8, 30, tzinfo=timezone.utc)

    assert business_seconds_between(
        start,
        end,
        timezone_name="Europe/Zurich",
        weekly_schedule=business_schedule(),
        holidays=[],
    ) == 7200
    assert business_seconds_between(
        start,
        end,
        timezone_name="UTC",
        weekly_schedule={},
        holidays=[],
    ) == int((end - start).total_seconds())


def test_most_specific_revision_is_assigned_and_targeted(db_session):
    tenant, market = make_scope(db_session)
    policy = make_policy(db_session)
    revision = approved_revision(db_session, policy, tenant, market)
    ticket = make_ticket(db_session, tenant, market)

    selected = select_policy_revision(db_session, ticket)
    assert selected is not None
    assert selected[0].id == revision.id

    apply_policy_to_ticket(ticket, policy, db=db_session)
    assignment = db_session.query(TicketSLAAssignment).one()
    target = db_session.query(TicketSLATarget).one()

    assert assignment.policy_revision_id == revision.id
    assert assignment.snapshot_json["risk_window_minutes"] == 45
    assert ensure_utc(target.first_response_due_at) == datetime(
        2026,
        8,
        3,
        8,
        0,
        tzinfo=timezone.utc,
    )
    assert ensure_utc(target.first_response_risk_at) == datetime(
        2026,
        8,
        3,
        7,
        15,
        tzinfo=timezone.utc,
    )
    assert ticket.first_response_due_at == target.first_response_due_at


def test_assignment_is_immutable_when_priority_changes(db_session):
    tenant, market = make_scope(db_session)
    medium = make_policy(db_session, TicketPriority.medium)
    medium_revision = approved_revision(db_session, medium, tenant, market)
    urgent = make_policy(db_session, TicketPriority.urgent)
    approved_revision(
        db_session,
        urgent,
        tenant,
        market,
        first_minutes=15,
        resolution_minutes=120,
        risk_window=10,
    )
    ticket = make_ticket(db_session, tenant, market)
    apply_policy_to_ticket(ticket, medium, db=db_session)
    assignment_id = db_session.query(TicketSLAAssignment.id).scalar()

    ticket.priority = TicketPriority.urgent
    apply_policy_to_ticket(ticket, urgent, db=db_session)
    assignment = db_session.query(TicketSLAAssignment).one()

    assert assignment.id == assignment_id
    assert assignment.policy_revision_id == medium_revision.id
    assert assignment.snapshot_json["priority"] == "medium"
    assert ticket.sla_policy_id == medium.id


def test_pause_compensates_only_business_time(db_session):
    tenant, market = make_scope(db_session)
    policy = make_policy(db_session)
    approved_revision(db_session, policy, tenant, market)
    ticket = make_ticket(db_session, tenant, market)
    apply_policy_to_ticket(ticket, policy, db=db_session)
    original_due = ensure_utc(
        db_session.query(TicketSLATarget).one().first_response_due_at
    )

    db_session.add(
        TicketSLAPauseInterval(
            ticket_id=ticket.id,
            reason_code="waiting_customer",
            started_at=datetime(
                2026,
                7,
                31,
                14,
                30,
                tzinfo=timezone.utc,
            ),
            ended_at=datetime(
                2026,
                8,
                3,
                8,
                30,
                tzinfo=timezone.utc,
            ),
            created_at=datetime(
                2026,
                7,
                31,
                14,
                30,
                tzinfo=timezone.utc,
            ),
        )
    )
    db_session.flush()
    apply_policy_to_ticket(
        ticket,
        policy,
        now=datetime(2026, 8, 3, 9, 0, tzinfo=timezone.utc),
        db=db_session,
    )
    target = db_session.query(TicketSLATarget).one()

    assert target.paused_seconds == 7200
    assert ensure_utc(target.first_response_due_at) == original_due + timedelta(
        hours=2
    )

    target.first_response_risk_at = datetime.now(timezone.utc) - timedelta(
        seconds=1
    )
    db_session.flush()
    rows = db_session.query(Ticket).filter(
        sla_risk_filter(datetime.now(timezone.utc))
    ).all()
    assert [row.id for row in rows] == [ticket.id]


def test_customer_tier_revision_fails_closed_instead_of_silent_miss(db_session):
    tenant, market = make_scope(db_session)
    policy = make_policy(db_session)
    approved_revision(
        db_session,
        policy,
        tenant,
        market,
        customer_tier="gold",
    )
    ticket = make_ticket(db_session, tenant, market)

    with pytest.raises(SLAConfigurationError) as exc:
        select_policy_revision(db_session, ticket)
    assert str(exc.value) == "sla_customer_tier_not_supported"
