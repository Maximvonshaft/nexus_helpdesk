from __future__ import annotations

import os
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/business_outcome_metric_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.db import Base  # noqa: E402
from app.enums import (  # noqa: E402
    SourceChannel,
    TicketPriority,
    TicketSource,
    TicketStatus,
)
from app.models import Customer, Tenant, Ticket, TicketComment  # noqa: E402
from app.models_case_governance import CaseOutcomeRecord  # noqa: E402
from app.services.business_outcome_metrics import (  # noqa: E402
    build_business_outcome_metrics,
)
from app.utils.time import utc_now  # noqa: E402
from app.webchat_models import WebchatHandoffRequest  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'metrics.db'}",
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
        tenant_key="metrics-tenant",
        display_name="Metrics Tenant",
        is_active=True,
    )
    db.add(tenant)
    db.flush()
    customer = Customer(
        tenant_id=tenant.id,
        tenant_assignment_source="test",
        tenant_assignment_version="v1",
        name="Metrics Customer",
        email="metrics@example.test",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db.add(customer)
    db.flush()
    return tenant, customer


def make_ticket(db, tenant, customer, *, suffix: str, created_offset_days: int):
    created = utc_now() - timedelta(days=created_offset_days)
    ticket = Ticket(
        tenant_id=tenant.id,
        tenant_assignment_source="test",
        tenant_assignment_version="v1",
        ticket_no=f"METRIC-{suffix}",
        title=f"Metric {suffix}",
        description=f"Metric {suffix}",
        customer_id=customer.id,
        source=TicketSource.manual,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        status=TicketStatus.closed,
        created_at=created,
        updated_at=created,
        closed_at=created + timedelta(hours=1),
    )
    db.add(ticket)
    db.flush()
    return ticket


def outcome(
    db,
    ticket,
    *,
    sequence: int,
    record_type: str,
    state: str,
    occurred_at,
    parent_record_id=None,
    payload=None,
):
    row = CaseOutcomeRecord(
        ticket_id=ticket.id,
        sequence=sequence,
        record_type=record_type,
        state=state,
        idempotency_key=f"{ticket.id}:{sequence}",
        parent_record_id=parent_record_id,
        source_kind="test",
        source_id=f"source-{ticket.id}-{sequence}",
        payload_json=payload or {},
        occurred_at=occurred_at,
        created_by=None,
        created_at=occurred_at,
    )
    db.add(row)
    db.flush()
    return row


def metric(payload, key):
    return next(item for item in payload["items"] if item["key"] == key)


def test_outcomes_use_explicit_denominators_and_reopen_window(db_session):
    tenant, customer = make_scope(db_session)
    current = make_ticket(
        db_session,
        tenant,
        customer,
        suffix="current",
        created_offset_days=5,
    )
    closed_at = current.closed_at
    outcome(
        db_session,
        current,
        sequence=1,
        record_type="closure_assessment",
        state="closed",
        occurred_at=closed_at,
    )
    outcome(
        db_session,
        current,
        sequence=2,
        record_type="customer_notification",
        state="delivered",
        occurred_at=closed_at,
    )
    attempt = outcome(
        db_session,
        current,
        sequence=3,
        record_type="execution_attempt",
        state="succeeded",
        occurred_at=closed_at,
        payload={"action_class": "tracking_lookup"},
    )
    outcome(
        db_session,
        current,
        sequence=4,
        record_type="operational_outcome",
        state="confirmed",
        occurred_at=closed_at,
        parent_record_id=attempt.id,
        payload={"outcome_level": "business_result_confirmed"},
    )
    outcome(
        db_session,
        current,
        sequence=5,
        record_type="closure_assessment",
        state="reopened",
        occurred_at=closed_at + timedelta(hours=48),
    )
    db_session.add(
        TicketComment(
            ticket_id=current.id,
            author_id=None,
            body="One human touch",
            created_at=closed_at,
            updated_at=closed_at,
        )
    )
    db_session.flush()

    payload = build_business_outcome_metrics(
        db_session,
        visible_query=db_session.query(Ticket).filter(Ticket.tenant_id == tenant.id),
        tenant_id=tenant.id,
    )

    safe = metric(payload, "safe_effective_closure_rate")
    reopen = metric(payload, "reopen_72h_rate")
    notification = metric(payload, "customer_notification_compliance")
    action = metric(payload, "action_operational_completion_rate")

    assert safe["numerator"] == 1
    assert safe["denominator"] == 1
    assert safe["value"] == 1.0
    assert reopen["numerator"] == 1
    assert reopen["denominator"] == 1
    assert reopen["value"] == 1.0
    assert reopen["status"] == "danger"
    assert notification["value"] == 1.0
    assert action["value"] == 1.0
    assert payload["contains_customer_data"] is False


def test_previous_window_produces_direction_aware_trend(db_session):
    tenant, customer = make_scope(db_session)
    previous = make_ticket(
        db_session,
        tenant,
        customer,
        suffix="previous",
        created_offset_days=40,
    )
    current = make_ticket(
        db_session,
        tenant,
        customer,
        suffix="current",
        created_offset_days=4,
    )
    outcome(
        db_session,
        previous,
        sequence=1,
        record_type="provider_receipt",
        state="failed",
        occurred_at=previous.closed_at,
    )
    outcome(
        db_session,
        current,
        sequence=1,
        record_type="provider_receipt",
        state="succeeded",
        occurred_at=current.closed_at,
    )

    payload = build_business_outcome_metrics(
        db_session,
        visible_query=db_session.query(Ticket).filter(Ticket.tenant_id == tenant.id),
        tenant_id=tenant.id,
    )
    provider = metric(payload, "provider_failure_rate")

    assert provider["value"] == 0.0
    assert provider["previous_value"] == 1.0
    assert provider["trend"] == "improving"
    assert provider["status"] == "success"


def test_no_samples_are_unavailable_not_false_success(db_session):
    tenant, _ = make_scope(db_session)

    payload = build_business_outcome_metrics(
        db_session,
        visible_query=db_session.query(Ticket).filter(Ticket.tenant_id == tenant.id),
        tenant_id=tenant.id,
    )

    for item in payload["items"]:
        assert item["value"] is None
        assert item["status"] == "unavailable"
        assert item["denominator"] == 0


def test_handoff_wait_p90_uses_accepted_requests(db_session):
    tenant, customer = make_scope(db_session)
    ticket = make_ticket(
        db_session,
        tenant,
        customer,
        suffix="handoff",
        created_offset_days=2,
    )
    requested_at = ticket.created_at
    for index, wait in enumerate((10, 20, 90), start=1):
        db_session.add(
            WebchatHandoffRequest(
                conversation_id=100 + index,
                ticket_id=ticket.id,
                source="customer",
                trigger_type="customer_requested_human",
                status="closed",
                requested_by_actor_type="visitor",
                requested_at=requested_at + timedelta(minutes=index),
                accepted_at=requested_at + timedelta(minutes=index, seconds=wait),
                closed_at=requested_at + timedelta(minutes=index + 1),
                lock_version=1,
                created_at=requested_at,
                updated_at=requested_at,
            )
        )
    db_session.flush()

    payload = build_business_outcome_metrics(
        db_session,
        visible_query=db_session.query(Ticket).filter(Ticket.tenant_id == tenant.id),
        tenant_id=tenant.id,
    )
    wait_metric = metric(payload, "handoff_wait_p90_seconds")

    assert wait_metric["value"] == 90.0
    assert wait_metric["numerator"] == 3
    assert wait_metric["denominator"] == 3
    assert wait_metric["status"] == "warning"
