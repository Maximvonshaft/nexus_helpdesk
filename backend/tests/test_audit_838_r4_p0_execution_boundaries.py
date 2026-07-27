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
    "sqlite:////tmp/audit-838-r4-p0.db",
)

from app.db import Base
from app.enums import (
    JobStatus,
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
    OutboundEmailAccount,
    Team,
    Tenant,
    Ticket,
    User,
)
from app.models_channel_intake import EmailIntakeQuarantine
from app.models_job_scope import BackgroundJobScope
from app.services.background_job_execution_scope import (
    BackgroundJobExecutionScopeError,
    claim_executable_background_jobs,
    require_executable_background_job_scope,
)
from app.services.background_job_scope import install_background_job_scope_events
from app.services.customer_identity_service import resolve_or_create_customer
from app.services.email_mailbox_polling_service import (
    ParsedMailboxMessage,
    _resolve_ticket,
    poll_imap_account,
)
from app.services.whatsapp_native_inbound import ingest_whatsapp_native_inbound
from app.api.operator_agent_state import _managed_operator

register_all_models()
install_background_job_scope_events()


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'audit-838-r4-p0.db'}",
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


def _ownership(tenant: Tenant, suffix: str) -> dict[str, object]:
    return {
        "tenant_id": tenant.id,
        "tenant_assignment_source": "fixture",
        "tenant_assignment_version": "sha256:" + suffix.lower()[0] * 64,
    }


def _org(db, tenant: Tenant, suffix: str):
    ownership = _ownership(tenant, suffix)
    market = Market(
        code=f"R4-{suffix}",
        name=f"R4 Market {suffix}",
        country_code="ME",
        is_active=True,
        **ownership,
    )
    db.add(market)
    db.flush()
    team = Team(
        name=f"R4 Team {suffix}",
        market_id=market.id,
        is_active=True,
        **ownership,
    )
    db.add(team)
    db.flush()
    user = User(
        username=f"r4-{suffix.lower()}",
        display_name=f"R4 {suffix}",
        email=f"r4-{suffix.lower()}@invalid.test",
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
        ticket_no=f"R4-{suffix}",
        title=f"R4 Ticket {suffix}",
        description="Audit 838 R4 proof",
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


def test_unresolved_and_missing_scope_jobs_are_never_claimed(db_session):
    tenant = _tenant(db_session, "r4-worker")
    _market, _team, _user, _customer, ticket = _org(db_session, tenant, "WORKER")
    executable = BackgroundJob(
        queue_name="speedaf_work_order",
        job_type="speedaf.work_order.create",
        payload_json=json.dumps({"ticket_id": ticket.id}),
        status=JobStatus.pending,
        max_attempts=3,
    )
    unresolved = BackgroundJob(
        queue_name="unknown",
        job_type="future.unknown",
        payload_json=json.dumps({"customer_id": 999999}),
        status=JobStatus.pending,
        max_attempts=3,
    )
    db_session.add_all([executable, unresolved])
    db_session.flush()
    missing = BackgroundJob(
        queue_name="missing-scope",
        job_type="future.missing_scope",
        payload_json="{}",
        status=JobStatus.pending,
        max_attempts=3,
    )
    db_session.add(missing)
    db_session.flush()
    db_session.query(BackgroundJobScope).filter(
        BackgroundJobScope.job_id == missing.id
    ).delete(synchronize_session=False)
    db_session.commit()

    claimed = claim_executable_background_jobs(
        db_session,
        worker_id="r4-worker",
        limit=10,
        job_types={
            "speedaf.work_order.create",
            "future.unknown",
            "future.missing_scope",
        },
    )

    assert [row.id for row in claimed] == [executable.id]
    assert db_session.get(BackgroundJob, unresolved.id).status == JobStatus.pending
    assert db_session.get(BackgroundJob, missing.id).status == JobStatus.pending


def test_execution_revalidates_scope_against_current_resource_ownership(db_session):
    tenant_a = _tenant(db_session, "r4-scope-a")
    tenant_b = _tenant(db_session, "r4-scope-b")
    _market_a, _team_a, _user_a, _customer_a, ticket_a = _org(
        db_session, tenant_a, "SA"
    )
    _org(db_session, tenant_b, "SB")
    job = BackgroundJob(
        queue_name="speedaf_work_order",
        job_type="speedaf.work_order.create",
        payload_json=json.dumps({"ticket_id": ticket_a.id}),
        status=JobStatus.pending,
        max_attempts=3,
    )
    db_session.add(job)
    db_session.flush()
    assert db_session.get(BackgroundJobScope, job.id).tenant_id == tenant_a.id

    ticket_a.tenant_id = tenant_b.id
    db_session.flush()

    with pytest.raises(BackgroundJobExecutionScopeError):
        require_executable_background_job_scope(db_session, job)


def test_customer_identity_is_atomic_and_tenant_scoped(db_session):
    tenant_a = _tenant(db_session, "r4-identity-a")
    tenant_b = _tenant(db_session, "r4-identity-b")

    first = resolve_or_create_customer(
        db_session,
        tenant_id=tenant_a.id,
        identity_type="email",
        identity_value=" Customer@Example.com ",
        display_name="Customer A",
        source="email",
    )
    same = resolve_or_create_customer(
        db_session,
        tenant_id=tenant_a.id,
        identity_type="email",
        identity_value="customer@example.com",
        display_name="Duplicate A",
        source="email",
    )
    other_tenant = resolve_or_create_customer(
        db_session,
        tenant_id=tenant_b.id,
        identity_type="email",
        identity_value="customer@example.com",
        display_name="Customer B",
        source="email",
    )

    assert same.id == first.id
    assert other_tenant.id != first.id
    assert first.tenant_id == tenant_a.id
    assert other_tenant.tenant_id == tenant_b.id


def test_native_whatsapp_uses_account_tenant_and_conversation_first(db_session):
    tenant = _tenant(db_session, "r4-whatsapp")
    market, _team, _user, _customer, _ticket = _org(db_session, tenant, "WA")
    account = ChannelAccount(
        provider=SourceChannel.whatsapp.value,
        account_id="r4-wa-account",
        display_name="R4 WhatsApp",
        market_id=market.id,
        is_active=True,
        **_ownership(tenant, "WA"),
    )
    db_session.add(account)
    db_session.flush()

    result = ingest_whatsapp_native_inbound(
        db_session,
        {
            "account_id": account.account_id,
            "external_message_id": "wamid-r4-1",
            "chat_jid": "15551234567@s.whatsapp.net",
            "sender_jid": "15551234567@s.whatsapp.net",
            "sender_phone": "+15551234567",
            "body_text": "Where is my order?",
            "received_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    assert result.ticket_id is None
    assert result.conversation_id is not None
    from app.models_agent_routing import ConversationControl
    from app.webchat_models import WebchatConversation

    conversation = db_session.get(WebchatConversation, result.conversation_id)
    control = (
        db_session.query(ConversationControl)
        .filter(ConversationControl.conversation_id == conversation.id)
        .one()
    )
    customer = db_session.get(Customer, control.customer_id)
    assert conversation.tenant_key == tenant.tenant_key
    assert conversation.channel_key == SourceChannel.whatsapp.value
    assert conversation.ticket_id is None
    assert customer.tenant_id == tenant.id


def test_imap_resolution_cannot_cross_tenant(db_session):
    tenant_a = _tenant(db_session, "r4-email-a")
    tenant_b = _tenant(db_session, "r4-email-b")
    market_a, _team_a, user_a, _customer_a, _ticket_a = _org(
        db_session, tenant_a, "EA"
    )
    _market_b, _team_b, _user_b, customer_b, ticket_b = _org(
        db_session, tenant_b, "EB"
    )
    customer_b.email = "customer@example.com"
    customer_b.email_normalized = "customer@example.com"
    account = OutboundEmailAccount(
        display_name="Tenant A mailbox",
        host="smtp.invalid.test",
        port=587,
        username="support-a",
        from_address="support-a@example.com",
        security_mode="starttls",
        is_active=True,
        priority=1,
        market_id=market_a.id,
        inbound_enabled=True,
        imap_host="imap.invalid.test",
        imap_port=993,
        imap_username="support-a",
        imap_security_mode="ssl",
        created_by=user_a.id,
        updated_by=user_a.id,
    )
    db_session.add(account)
    db_session.flush()
    message = ParsedMailboxMessage(
        uid="1",
        from_address="customer@example.com",
        from_name="Customer",
        to_address="support-a@example.com",
        cc=None,
        subject=f"Re: nexusdesk-ticket-{ticket_b.id}",
        body="Tenant B ticket must not be visible here.",
        message_id="<r4-email-1@example.com>",
        references=None,
        in_reply_to=None,
        received_at=datetime.now(timezone.utc),
        raw_preview="Tenant B ticket must not be visible here.",
    )

    assert _resolve_ticket(db_session, account, message) is None


def test_unmatched_imap_message_is_durably_quarantined_before_cursor_advances(
    db_session,
):
    tenant = _tenant(db_session, "r4-email-quarantine")
    market, _team, user, _customer, _ticket = _org(db_session, tenant, "EQ")
    account = OutboundEmailAccount(
        display_name="Quarantine mailbox",
        host="smtp.invalid.test",
        port=587,
        username="support-q",
        from_address="support-q@example.com",
        security_mode="starttls",
        is_active=True,
        priority=1,
        market_id=market.id,
        inbound_enabled=True,
        imap_host="imap.invalid.test",
        imap_port=993,
        imap_username="support-q",
        imap_password_encrypted="ciphertext",
        imap_security_mode="ssl",
        created_by=user.id,
        updated_by=user.id,
    )
    db_session.add(account)
    db_session.flush()

    raw_message = (
        b"From: New Customer <new@example.com>\r\n"
        b"To: support-q@example.com\r\n"
        b"Subject: First contact\r\n"
        b"Message-ID: <r4-first-contact@example.com>\r\n"
        b"Date: Mon, 27 Jul 2026 10:00:00 +0000\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
        b"I need help with a delivery."
    )

    class FakeMailbox:
        def select(self, _mailbox):
            return "OK"

        def uid(self, command, *args):
            if command == "search":
                return "OK", [b"1"]
            if command == "fetch":
                return "OK", [(b"1 (RFC822)", raw_message)]
            raise AssertionError(command)

        def logout(self):
            return "BYE"

    result = poll_imap_account(db_session, account, client=FakeMailbox())

    assert result.fetched == 1
    assert result.quarantined == 1
    assert result.cursor == "1"
    quarantine = db_session.query(EmailIntakeQuarantine).one()
    assert quarantine.tenant_id == tenant.id
    assert quarantine.account_id == account.id
    assert quarantine.status == "pending_intake"
    assert quarantine.provider_message_id == "imap:1:1"


def test_managed_operator_lookup_is_tenant_scoped(db_session):
    tenant_a = _tenant(db_session, "r4-operator-a")
    tenant_b = _tenant(db_session, "r4-operator-b")
    _market_a, _team_a, user_a, _customer_a, _ticket_a = _org(
        db_session, tenant_a, "OA"
    )
    _market_b, _team_b, user_b, _customer_b, _ticket_b = _org(
        db_session, tenant_b, "OB"
    )

    with pytest.raises(HTTPException) as exc:
        _managed_operator(db_session, actor=user_a, user_id=user_b.id)
    assert exc.value.status_code == 404
