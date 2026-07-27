from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/audit-838-processing-purpose.db",
)

from app.db import Base
from app.enums import (
    JobStatus,
    MessageStatus,
    SourceChannel,
    TicketPriority,
    TicketSource,
    TicketStatus,
    UserRole,
)
from app.model_registry import register_all_models
from app.models import (
    BackgroundJob,
    Customer,
    Market,
    Team,
    Tenant,
    Ticket,
    TicketOutboundMessage,
    User,
)
from app.models_case_governance import DataSubjectRequest
from app.models_job_scope import BackgroundJobScope
from app.models_privacy_runtime import DataProcessingRestriction
from app.services.background_job_scope import install_background_job_scope_events
from app.services.data_subject_action_service import DataProcessingRestricted
from app.services.processing_purpose_enforcement import (
    assert_declared_processing_purposes,
    install_processing_purpose_events,
)
from app.services.speedaf.action_service import SpeedafActionService
from app.services.speedaf.client import SpeedafMcpClient
from app.services.speedaf.schemas import SpeedafMcpConfig
from app.utils.time import utc_now

register_all_models()
install_background_job_scope_events()
install_processing_purpose_events()

ROOT = Path(__file__).resolve().parents[2]
PURPOSE_AUTHORITY = (
    ROOT / "config/privacy/processing-purpose-authority.v1.json"
)


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'processing-purpose.db'}",
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


def _scope(db):
    tenant = Tenant(
        tenant_key="audit-838-processing",
        display_name="Audit 838 Processing",
        is_active=True,
    )
    db.add(tenant)
    db.flush()
    ownership = {
        "tenant_id": tenant.id,
        "tenant_assignment_source": "fixture",
        "tenant_assignment_version": "v1",
    }
    market = Market(
        code="A838-P",
        name="Audit 838 Processing Market",
        country_code="ME",
        **ownership,
    )
    db.add(market)
    db.flush()
    team = Team(
        name="Audit 838 Processing Team",
        market_id=market.id,
        **ownership,
    )
    db.add(team)
    db.flush()
    user = User(
        username="audit-838-processing",
        display_name="Audit 838 Processing",
        email="audit-838-processing@invalid.test",
        password_hash="x",
        role=UserRole.admin,
        team_id=team.id,
        is_active=True,
        **ownership,
    )
    db.add(user)
    db.flush()
    customer = Customer(name="Audit 838 Customer", **ownership)
    db.add(customer)
    db.flush()
    ticket = Ticket(
        ticket_no="AUD838-PROCESSING",
        title="Processing purpose proof",
        description="Processing purpose proof",
        source=TicketSource.manual,
        source_channel=SourceChannel.internal,
        priority=TicketPriority.medium,
        status=TicketStatus.new,
        customer_id=customer.id,
        market_id=market.id,
        team_id=team.id,
        created_by=user.id,
        **ownership,
    )
    db.add(ticket)
    db.flush()
    return tenant, user, customer, ticket


def _request(db, tenant, customer, suffix: str):
    row = DataSubjectRequest(
        tenant_id=tenant.id,
        customer_id=customer.id,
        request_key=f"audit-838-{suffix}",
        request_type="restrict",
        status="qualified",
        scope_json={},
        received_at=utc_now(),
        due_at=utc_now() + timedelta(days=30),
        updated_at=utc_now(),
    )
    db.add(row)
    db.flush()
    return row


def _restriction(db, tenant, customer, request):
    row = DataProcessingRestriction(
        tenant_id=tenant.id,
        customer_id=customer.id,
        request_id=request.id,
        status="active",
        blocked_purposes_json=[
            "automated_ai",
            "provider_tool_execution",
            "analytics",
            "automatic_outbound",
        ],
        allowed_purposes_json=["human_support", "dsar", "retention"],
        reason_code="data_subject_requested_restriction",
        placed_at=utc_now(),
    )
    db.add(row)
    db.flush()
    return row


def test_restriction_blocks_new_automatic_external_outbound(db_session):
    tenant, user, customer, ticket = _scope(db_session)
    request = _request(db_session, tenant, customer, "new-outbound")
    _restriction(db_session, tenant, customer, request)
    db_session.commit()
    ticket_id = ticket.id
    user_id = user.id

    automatic = TicketOutboundMessage(
        ticket_id=ticket_id,
        channel=SourceChannel.email,
        status=MessageStatus.pending,
        body="automatic effect",
        origin="business_system",
        created_by=None,
    )
    db_session.add(automatic)
    with pytest.raises(DataProcessingRestricted):
        db_session.flush()
    db_session.rollback()

    human = TicketOutboundMessage(
        ticket_id=ticket_id,
        channel=SourceChannel.email,
        status=MessageStatus.pending,
        body="human support",
        origin="human_agent",
        created_by=user_id,
    )
    db_session.add(human)
    db_session.flush()
    assert human.id is not None


def test_restriction_cancels_already_pending_effects(db_session):
    tenant, _user, customer, ticket = _scope(db_session)
    pending_message = TicketOutboundMessage(
        ticket_id=ticket.id,
        channel=SourceChannel.email,
        status=MessageStatus.pending,
        body="queued automatic effect",
        origin="business_system",
        created_by=None,
    )
    pending_job = BackgroundJob(
        queue_name="speedaf_work_order",
        job_type="speedaf.work_order.create",
        payload_json=json.dumps({"ticket_id": ticket.id}),
        status=JobStatus.pending,
        max_attempts=3,
    )
    db_session.add_all([pending_message, pending_job])
    db_session.flush()
    assert (
        db_session.get(BackgroundJobScope, pending_job.id).customer_id
        == customer.id
    )

    request = _request(db_session, tenant, customer, "cancel-pending")
    _restriction(db_session, tenant, customer, request)
    db_session.expire_all()

    assert (
        db_session.get(TicketOutboundMessage, pending_message.id).status
        == MessageStatus.dead
    )
    assert db_session.get(BackgroundJob, pending_job.id).status == JobStatus.dead
    assert (
        db_session.get(BackgroundJob, pending_job.id).last_error
        == "data_processing_restricted"
    )


class _FakeSpeedafClient(SpeedafMcpClient):
    def __init__(self):
        super().__init__(
            SpeedafMcpConfig(
                enabled=True,
                base_url="https://invalid.test",
                app_code="audit-838",
                secret_key=None,
                customer_code="AUD838",
                platform_source="API KEY",
                lookup_caller_id=None,
                timeout_seconds=1,
                country_code_default="ME",
                content_type="text/plain",
                data_mode="string",
                require_sign=False,
            )
        )
        self.calls: list[tuple[str, dict]] = []

    def post(self, path, data):
        self.calls.append((path, data))
        return self.normalize_response(
            {"success": True, "data": {"workOrderCode": "WO-AUD838"}},
            status_code=200,
        )


def test_speedaf_provider_guard_is_non_retryable_and_makes_no_network_call(
    monkeypatch,
):
    monkeypatch.setenv("SPEEDAF_WORK_ORDER_CREATE_ENABLED", "true")

    def _blocked(**_kwargs):
        raise DataProcessingRestricted(
            customer_id=1,
            purpose="provider_tool_execution",
            restriction_id=1,
        )

    monkeypatch.setattr(
        "app.services.speedaf.action_service."
        "ensure_ticket_processing_allowed_fresh",
        _blocked,
    )
    client = _FakeSpeedafClient()
    result = SpeedafActionService(client, ticket_id=1).create_work_order(
        waybill_code="AUD838",
        work_order_type="WT0103-05",
        description="blocked",
        caller_id="10000",
    )

    assert result.ok is False
    assert result.status == "blocked"
    assert result.error_code == "data_processing_restricted"
    assert result.retryable is False
    assert client.calls == []


def test_processing_purpose_authority_is_complete():
    payload = json.loads(PURPOSE_AUTHORITY.read_text(encoding="utf-8"))
    assert payload["schema"] == "nexus.processing-purpose-authority.v1"
    assert payload["default_posture"] == "fail_closed"
    assert_declared_processing_purposes(payload["purposes"])
    assert payload["purposes"]["model_training"]["implemented"] is False
    assert payload["purposes"]["marketing"]["implemented"] is False
