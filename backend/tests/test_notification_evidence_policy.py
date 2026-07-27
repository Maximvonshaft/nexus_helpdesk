from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/notification-evidence-policy.db",
)

from app.db import Base
from app.enums import (
    MessageStatus,
    SourceChannel,
    TicketPriority,
    TicketSource,
)
from app.models import Ticket, TicketOutboundMessage
from app.models_case_governance import CaseOutcomeRecord
from app.services.nexus_osr.business_scenarios import (
    BusinessScenarioDefinition,
    ScenarioLifecycle,
    ScenarioReadiness,
)
from app.services.notification_evidence_policy import (
    apply_notification_evidence_policy,
)
from app.services.ticket_closure_readiness import ClosureSnapshot
from app.utils.time import utc_now


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'notification.db'}",
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
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _ticket(db) -> Ticket:
    row = Ticket(
        ticket_no="NOTIFY-1",
        title="Notification evidence",
        description="Notification evidence",
        source=TicketSource.manual,
        source_channel=SourceChannel.email,
        priority=TicketPriority.medium,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db.add(row)
    db.flush()
    return row


def _scenario(
    *,
    policy: str,
    waiver_reasons: tuple[str, ...] = (),
) -> BusinessScenarioDefinition:
    effective = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return BusinessScenarioDefinition(
        scenario_key="tracking_status_inquiry",
        issue_type_aliases=(),
        trigger_sources=("customer_message",),
        required_fact_classes=(),
        required_customer_inputs=(),
        risk_level="low",
        escalation_policy_key=None,
        owner_queue_key="customer_support",
        required_capabilities=("ticket.read",),
        allowed_action_classes=("notify_customer",),
        required_action_classes=("notify_customer",),
        blocked_action_classes=(),
        notification_policy=policy,
        allowed_no_notification_reasons=waiver_reasons,
        terminal_behavior="closeable",
        required_outcome_levels=("customer_notified",),
        completion_rules=("notification_policy_satisfied",),
        definition_of_done="Customer notification is confirmed.",
        observation_period_seconds=0,
        reopen_conditions=("customer_disputes_resolution",),
        cancellation_semantics="cancel_only_with_reason",
        metrics=("customer_notification_compliance",),
        scope_mode="inherit_resolved_scope",
        lifecycle=ScenarioLifecycle(
            status="approved",
            owner="customer_operations",
            approved_at=effective,
            effective_from=effective,
            review_due=datetime(2027, 1, 1, tzinfo=timezone.utc),
            expires_at=None,
            supersedes=None,
        ),
    )


def _snapshot(scenario: BusinessScenarioDefinition) -> ClosureSnapshot:
    readiness = ScenarioReadiness(
        scenario_key=scenario.scenario_key,
        closure_ready=True,
        missing_fact_classes=(),
        missing_customer_inputs=(),
        missing_action_classes=(),
        missing_outcome_levels=(),
        notification_satisfied=True,
        blocked_reasons=(),
    )
    return ClosureSnapshot(
        scenario=scenario,
        readiness=readiness,
        receipt={
            "schema": "nexus.ticket-closure-receipt.v2",
            "scenario_key": scenario.scenario_key,
            "readiness": readiness.as_dict(),
            "evidence": {"contains_payloads": False},
            "receipt_sha256": "pre-policy",
        },
    )


def test_sent_is_attempt_only_and_blocks_required_notification(db_session):
    ticket = _ticket(db_session)
    db_session.add(
        TicketOutboundMessage(
            ticket_id=ticket.id,
            channel=SourceChannel.email,
            status=MessageStatus.sent,
            body="sent only",
            delivery_status="sent",
            created_at=utc_now(),
            updated_at=utc_now(),
        )
    )
    db_session.flush()

    governed = apply_notification_evidence_policy(
        db_session,
        ticket=ticket,
        snapshot=_snapshot(_scenario(policy="required")),
    )

    assert governed.readiness.closure_ready is False
    assert governed.readiness.notification_satisfied is False
    assert "notification_delivery_unconfirmed" in governed.readiness.blocked_reasons
    assert governed.receipt["evidence"]["notification"]["state"] == "attempted"
    assert governed.receipt["receipt_sha256"] != "pre-policy"


def test_delivered_or_confirmed_evidence_satisfies_required_notification(db_session):
    ticket = _ticket(db_session)
    db_session.add(
        TicketOutboundMessage(
            ticket_id=ticket.id,
            channel=SourceChannel.email,
            status=MessageStatus.sent,
            body="delivered",
            delivery_status="delivered",
            created_at=utc_now(),
            updated_at=utc_now(),
        )
    )
    db_session.flush()

    governed = apply_notification_evidence_policy(
        db_session,
        ticket=ticket,
        snapshot=_snapshot(_scenario(policy="required")),
    )

    assert governed.readiness.closure_ready is True
    assert governed.readiness.notification_satisfied is True
    assert governed.receipt["evidence"]["notification"]["state"] == "confirmed"


def test_conditional_waiver_requires_allowed_structured_reason(db_session):
    ticket = _ticket(db_session)
    db_session.add(
        CaseOutcomeRecord(
            ticket_id=ticket.id,
            sequence=1,
            record_type="customer_notification",
            state="waived",
            idempotency_key="notification-waiver",
            payload_json={"waiver_reason": "no_contact_method"},
            occurred_at=utc_now(),
            created_at=utc_now(),
        )
    )
    db_session.flush()

    governed = apply_notification_evidence_policy(
        db_session,
        ticket=ticket,
        snapshot=_snapshot(
            _scenario(
                policy="required_if_contactable",
                waiver_reasons=("no_contact_method",),
            )
        ),
    )
    forbidden_for_required = apply_notification_evidence_policy(
        db_session,
        ticket=ticket,
        snapshot=_snapshot(_scenario(policy="required")),
    )

    assert governed.readiness.closure_ready is True
    assert governed.receipt["evidence"]["notification"]["state"] == "waived"
    assert forbidden_for_required.readiness.closure_ready is False


def test_prohibited_policy_requires_zero_notification_attempts(db_session):
    ticket = _ticket(db_session)
    empty = apply_notification_evidence_policy(
        db_session,
        ticket=ticket,
        snapshot=_snapshot(_scenario(policy="prohibited")),
    )
    assert empty.readiness.closure_ready is True

    db_session.add(
        TicketOutboundMessage(
            ticket_id=ticket.id,
            channel=SourceChannel.email,
            status=MessageStatus.pending,
            body="must not be sent",
            delivery_status="queued",
            created_at=utc_now(),
            updated_at=utc_now(),
        )
    )
    db_session.flush()
    attempted = apply_notification_evidence_policy(
        db_session,
        ticket=ticket,
        snapshot=_snapshot(_scenario(policy="prohibited")),
    )

    assert attempted.readiness.closure_ready is False
    assert attempted.readiness.notification_satisfied is False
    assert attempted.receipt["evidence"]["notification"]["state"] == "attempted"
