from __future__ import annotations

import os
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/retention_guard_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.db import Base  # noqa: E402
from app.enums import (  # noqa: E402
    NoteVisibility,
    SourceChannel,
    TicketPriority,
    TicketSource,
    TicketStatus,
    UserRole,
)
from app.models import Customer, Tenant, Ticket, TicketAttachment, User  # noqa: E402
from app.services.data_lifecycle_service import (  # noqa: E402
    DataLifecycleError,
    create_retention_policy,
    plan_retention_execution,
)
from app.services.retention_execution_guard import (  # noqa: E402
    apply_retention_execution,
)
from app.utils.time import utc_now  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'retention-guard.db'}",
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


def make_context(db):
    tenant = Tenant(
        tenant_key="retention-guard",
        display_name="Retention Guard",
        is_active=True,
    )
    db.add(tenant)
    db.flush()
    admin = User(
        tenant_id=tenant.id,
        tenant_assignment_source="test",
        tenant_assignment_version="v1",
        username="retention-admin",
        display_name="Retention Admin",
        email="retention-admin@example.test",
        password_hash="x",
        role=UserRole.admin,
        is_active=True,
    )
    customer = Customer(
        tenant_id=tenant.id,
        tenant_assignment_source="test",
        tenant_assignment_version="v1",
        name="Retention Customer",
        email="retention-customer@example.test",
        email_normalized="retention-customer@example.test",
        phone="+410000077",
        phone_normalized="+410000077",
        external_ref="retention-customer",
        created_at=utc_now() - timedelta(days=900),
        updated_at=utc_now() - timedelta(days=800),
    )
    db.add_all([admin, customer])
    db.flush()
    ticket = Ticket(
        tenant_id=tenant.id,
        tenant_assignment_source="test",
        tenant_assignment_version="v1",
        ticket_no="RET-GUARD-1",
        title="Old closed case",
        description="Old closed case",
        customer_id=customer.id,
        source=TicketSource.manual,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        status=TicketStatus.closed,
        created_at=utc_now() - timedelta(days=750),
        updated_at=utc_now() - timedelta(days=700),
        closed_at=utc_now() - timedelta(days=650),
    )
    db.add(ticket)
    db.flush()
    policy = create_retention_policy(
        db,
        actor=admin,
        resource_type="customer_profile",
        retention_days=365,
        legal_basis="contract retention expired",
    )
    execution = plan_retention_execution(
        db,
        actor=admin,
        policy_id=policy.id,
        execution_key="retention-guard-run",
    )
    assert execution.receipt_json["candidate_ids"] == [customer.id]
    return tenant, admin, customer, ticket, execution


def test_valid_dry_run_applies_once_and_returns_same_execution(db_session):
    _, admin, customer, _, execution = make_context(db_session)

    first = apply_retention_execution(
        db_session,
        actor=admin,
        execution_id=execution.id,
    )
    second = apply_retention_execution(
        db_session,
        actor=admin,
        execution_id=execution.id,
    )
    db_session.refresh(customer)

    assert first.id == second.id == execution.id
    assert first.status == "applied"
    assert first.affected_count == 1
    assert customer.email is None
    assert customer.phone is None
    assert customer.name.startswith("erased-customer-")


def test_tampered_dry_run_receipt_is_rejected_before_mutation(db_session):
    _, admin, customer, _, execution = make_context(db_session)
    original_email = customer.email
    execution.receipt_json = {
        **execution.receipt_json,
        "eligible_count": 99,
    }
    db_session.flush()

    with pytest.raises(
        DataLifecycleError,
        match="retention_dry_run_receipt_hash_mismatch",
    ):
        apply_retention_execution(
            db_session,
            actor=admin,
            execution_id=execution.id,
        )

    db_session.refresh(customer)
    assert customer.email == original_email
    assert execution.status == "dry_run"


def test_customer_data_drift_is_rejected_before_mutation(db_session):
    _, admin, customer, _, execution = make_context(db_session)
    original_email = customer.email
    customer.updated_at = utc_now()
    db_session.flush()

    with pytest.raises(DataLifecycleError, match="retention_candidate_data_drift"):
        apply_retention_execution(
            db_session,
            actor=admin,
            execution_id=execution.id,
        )

    db_session.refresh(customer)
    assert customer.email == original_email
    assert execution.status == "dry_run"


def test_new_active_case_or_attachment_blocks_old_dry_run(db_session):
    tenant, admin, customer, closed_ticket, execution = make_context(db_session)
    active = Ticket(
        tenant_id=tenant.id,
        tenant_assignment_source="test",
        tenant_assignment_version="v1",
        ticket_no="RET-GUARD-ACTIVE",
        title="New active case",
        description="Created after retention dry-run",
        customer_id=customer.id,
        source=TicketSource.manual,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        status=TicketStatus.in_progress,
    )
    db_session.add(active)
    db_session.flush()

    with pytest.raises(
        DataLifecycleError,
        match="retention_candidate_active_case_drift",
    ):
        apply_retention_execution(
            db_session,
            actor=admin,
            execution_id=execution.id,
        )

    active.status = TicketStatus.closed
    active.closed_at = utc_now()
    db_session.add(
        TicketAttachment(
            ticket_id=closed_ticket.id,
            uploaded_by=admin.id,
            file_name="new-evidence.pdf",
            storage_key="new-evidence.pdf",
            file_path=None,
            file_url=None,
            mime_type="application/pdf",
            file_size=10,
            visibility=NoteVisibility.external,
            created_at=utc_now(),
        )
    )
    db_session.flush()

    with pytest.raises(
        DataLifecycleError,
        match="retention_candidate_attachment_drift",
    ):
        apply_retention_execution(
            db_session,
            actor=admin,
            execution_id=execution.id,
        )

    db_session.refresh(customer)
    assert customer.email == "retention-customer@example.test"
    assert execution.status == "dry_run"
