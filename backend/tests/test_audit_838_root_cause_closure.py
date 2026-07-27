from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/audit-838-root-cause-tests.db",
)

from app.api.admin_tenant_query_scope import (
    _ADMIN_TENANT_ID,
    _ADMIN_TENANT_KEY,
    _set_scope,
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
    ChannelAccount,
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
from app.operator_models import OperatorQueueScopeGrant, OperatorTask
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
PURPOSE_AUTHORITY = ROOT / "config/privacy/processing-purpose-authority.v1.json"


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'audit-838.db'}",
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


def _tenant(db, key: str) -> Tenant:
    row = Tenant(tenant_key=key, display_name=key.title(), is_active=True)
    db.add(row)
    db.flush()
    return row


def _org(db, tenant: Tenant, suffix: str):
    ownership = {
        "tenant_id": tenant.id,
        "tenant_assignment_source": "fixture",
        "tenant_assignment_version": "sha256:" + (suffix.lower()[0] * 64),
    }
    market = Market(
        code=f"A838-{suffix}",
        name=f"Audit 838 Market {suffix}",
        country_code="ME",
        **ownership,
    )
    db.add(market)
    db.flush()
    team = Team(name=f"Audit 838 Team {suffix}", market_id=market.id, **ownership)
    db.add(team)
    db.flush()
    user = User(
        username=f"audit-838-{suffix.lower()}",
        display_name=f"Audit 838 {suffix}",
        email=f"audit-838-{suffix.lower()}@invalid.test",
        password_hash="x",
        role=UserRole.admin,
        team_id=team.id,
        is_active=True,
        **ownership,
    )
    db.add(user)
    db.flush()
    customer = Customer(name=f"Customer {suffix}", **ownership)
    db.add(customer)
    db.flush()
    ticket = Ticket(
        ticket_no=f"AUD838-{suffix}",
        title=f"Audit 838 Ticket {suffix}",
        description="Tenant isolation proof",
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
    return market, team, user, customer, ticket


def _scope(tenant: Tenant):
    return _set_scope(tenant.id, tenant.tenant_key)


def _reset_scope(tokens) -> None:
    tenant_id_token, tenant_key_token = tokens
    _ADMIN_TENANT_KEY.reset(tenant_key_token)
    _ADMIN_TENANT_ID.reset(tenant_id_token)


def _restriction_request(
    db,
    *,
    tenant: Tenant,
    customer: Customer,
    suffix: str,
) -> DataSubjectRequest:
    row = DataSubjectRequest(
        tenant_id=tenant.id,
        customer_id=customer.id,
        request_key=f"audit-838-restrict-{suffix}",
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


def _active_restriction(
    db,
    *,
    tenant: Tenant,
    customer: Customer,
    request: DataSubjectRequest,
) -> DataProcessingRestriction:
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


def test_background_job_scope_is_server_derived_and_fail_closed(db_session):
    tenant_a = _tenant(db_session, "audit-838-a")
    tenant_b = _tenant(db_session, "audit-838-b")
    _org(db_session, tenant_a, "A")
    _market_b, _team_b, _user_b, customer_b, ticket_b = _org(
        db_session,
        tenant_b,
        "B",
    )

    scoped = BackgroundJob(
        queue_name="speedaf_work_order",
        job_type="speedaf.work_order.create",
        payload_json=json.dumps({"ticket_id": ticket_b.id}),
        status=JobStatus.pending,
        max_attempts=3,
    )
    unknown = BackgroundJob(
        queue_name="unknown",
        job_type="future.unknown",
        payload_json=json.dumps({"customer_id": 999999}),
        status=JobStatus.pending,
        max_attempts=3,
    )
    db_session.add_all([scoped, unknown])
    db_session.flush()

    scoped_scope = db_session.get(BackgroundJobScope, scoped.id)
    unknown_scope = db_session.get(BackgroundJobScope, unknown.id)
    assert scoped_scope is not None
    assert scoped_scope.scope_type == "tenant"
    assert scoped_scope.tenant_id == tenant_b.id
    assert scoped_scope.customer_id == customer_b.id
    assert scoped_scope.purpose == "provider_tool_execution"
    assert unknown_scope is not None
    assert unknown_scope.scope_type == "unresolved"
    assert unknown_scope.tenant_id is None
    assert unknown_scope.purpose == "unclassified"

    tokens = _scope(tenant_a)
    try:
        assert db_session.query(BackgroundJob).filter(BackgroundJob.id == scoped.id).first() is None
        assert db_session.query(BackgroundJob).filter(BackgroundJob.id == unknown.id).first() is None
    finally:
        _reset_scope(tokens)

    tokens = _scope(tenant_b)
    try:
        assert db_session.query(BackgroundJob).filter(BackgroundJob.id == scoped.id).one().id == scoped.id
        assert db_session.query(BackgroundJob).filter(BackgroundJob.id == unknown.id).first() is None
    finally:
        _reset_scope(tokens)


def test_operator_queue_and_scope_grants_are_tenant_isolated(db_session):
    tenant_a = _tenant(db_session, "audit-838-op-a")
    tenant_b = _tenant(db_session, "audit-838-op-b")
    _market_a, _team_a, user_a, _customer_a, ticket_a = _org(db_session, tenant_a, "OPA")
    _market_b, _team_b, user_b, _customer_b, ticket_b = _org(db_session, tenant_b, "OPB")

    task_a = OperatorTask(
        source_type="ticket",
        source_id=str(ticket_a.id),
        ticket_id=ticket_a.id,
        task_type="review",
        status="pending",
    )
    task_b = OperatorTask(
        source_type="ticket",
        source_id=str(ticket_b.id),
        ticket_id=ticket_b.id,
        task_type="review",
        status="pending",
    )
    grant_a = OperatorQueueScopeGrant(
        user_id=user_a.id,
        tenant_key=tenant_a.tenant_key,
        country_code="ME",
        channel_key="web_chat",
        enabled=True,
        granted_by=user_a.id,
    )
    grant_b = OperatorQueueScopeGrant(
        user_id=user_b.id,
        tenant_key=tenant_b.tenant_key,
        country_code="ME",
        channel_key="web_chat",
        enabled=True,
        granted_by=user_b.id,
    )
    db_session.add_all([task_a, task_b, grant_a, grant_b])
    db_session.flush()

    tokens = _scope(tenant_a)
    try:
        assert [row.id for row in db_session.query(OperatorTask).order_by(OperatorTask.id).all()] == [task_a.id]
        assert [row.id for row in db_session.query(OperatorQueueScopeGrant).all()] == [grant_a.id]
    finally:
        _reset_scope(tokens)

    tokens = _scope(tenant_b)
    try:
        assert [row.id for row in db_session.query(OperatorTask).order_by(OperatorTask.id).all()] == [task_b.id]
        assert [row.id for row in db_session.query(OperatorQueueScopeGrant).all()] == [grant_b.id]
    finally:
        _reset_scope(tokens)


def test_admin_write_scope_stamps_tenant_and_rejects_cross_tenant_links(db_session):
    tenant_a = _tenant(db_session, "audit-838-write-a")
    tenant_b = _tenant(db_session, "audit-838-write-b")
    _market_a, _team_a, _user_a, _customer_a, _ticket_a = _org(db_session, tenant_a, "WA")
    market_b, _team_b, _user_b, _customer_b, _ticket_b = _org(db_session, tenant_b, "WB")

    tokens = _scope(tenant_a)
    try:
        market = Market(
            code="AUD838-NEW",
            name="Audit 838 New Market",
            country_code="ME",
            is_active=True,
        )
        db_session.add(market)
        db_session.flush()
        assert market.tenant_id == tenant_a.id
        assert market.tenant_assignment_source == "runtime_principal"

        db_session.add(
            ChannelAccount(
                provider="whatsapp",
                account_id="audit-838-cross-tenant",
                market_id=market_b.id,
                is_active=True,
            )
        )
        with pytest.raises(HTTPException) as exc:
            db_session.flush()
        assert exc.value.status_code == 409
        assert exc.value.detail["error_code"] == "admin_tenant_write_scope_conflict"
        db_session.rollback()
    finally:
        _reset_scope(tokens)


def test_restriction_blocks_new_automatic_external_outbound(db_session):
    tenant = _tenant(db_session, "audit-838-outbound")
    _market, _team, user, customer, ticket = _org(db_session, tenant, "OUT")
    request = _restriction_request(
        db_session,
        tenant=tenant,
        customer=customer,
        suffix="outbound",
    )
    _active_restriction(
        db_session,
        tenant=tenant,
        customer=customer,
        request=request,
    )

    db_session.add(
        TicketOutboundMessage(
            ticket_id=ticket.id,
            channel=SourceChannel.email,
            status=MessageStatus.pending,
            body="automatic effect",
            origin="business_system",
            created_by=None,
        )
    )
    with pytest.raises(DataProcessingRestricted):
        db_session.flush()
    db_session.rollback()

    human = TicketOutboundMessage(
        ticket_id=ticket.id,
        channel=SourceChannel.email,
        status=MessageStatus.pending,
        body="human support",
        origin="human_agent",
        created_by=user.id,
    )
    db_session.add(human)
    db_session.flush()
    assert human.id is not None


def test_restriction_cancels_already_pending_effects(db_session):
    tenant = _tenant(db_session, "audit-838-cancel")
    _market, _team, _user, customer, ticket = _org(db_session, tenant, "CAN")
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
    assert db_session.get(BackgroundJobScope, pending_job.id).customer_id == customer.id

    request = _restriction_request(
        db_session,
        tenant=tenant,
        customer=customer,
        suffix="cancel-pending",
    )
    _active_restriction(
        db_session,
        tenant=tenant,
        customer=customer,
        request=request,
    )
    db_session.expire_all()

    assert db_session.get(TicketOutboundMessage, pending_message.id).status == MessageStatus.dead
    assert db_session.get(BackgroundJob, pending_job.id).status == JobStatus.dead
    assert db_session.get(BackgroundJob, pending_job.id).last_error == "data_processing_restricted"


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


def test_speedaf_provider_guard_is_non_retryable_and_makes_no_network_call(monkeypatch):
    monkeypatch.setenv("SPEEDAF_WORK_ORDER_CREATE_ENABLED", "true")

    def _blocked(**_kwargs):
        raise DataProcessingRestricted(
            customer_id=1,
            purpose="provider_tool_execution",
            restriction_id=1,
        )

    monkeypatch.setattr(
        "app.services.speedaf.action_service.ensure_ticket_processing_allowed_fresh",
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
