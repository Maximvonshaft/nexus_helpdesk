from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.enums import (
    ConversationState,
    SourceChannel,
    TicketPriority,
    TicketSource,
    TicketStatus,
)
from app.model_registry import register_all_models
from app.models import (
    Customer,
    Market,
    OutboundEmailAccount,
    Team,
    Tenant,
    Ticket,
    TicketInboundEmailMessage,
    User,
)
from app.models_channel_intake import (
    CustomerIdentityBinding,
    EmailIntakeQuarantine,
)
from app.services.customer_identity_service import resolve_or_create_customer
from app.services.email_mailbox_polling_service import (
    ParsedMailboxMessage,
    _resolve_ticket,
    poll_imap_account,
)
from app.utils.time import utc_now

register_all_models()


@pytest.fixture()
def db_session(tmp_path):
    configured = os.getenv("DATABASE_URL", "").strip()
    is_postgres = configured.startswith("postgresql")
    database_url = (
        configured
        if is_postgres
        else f"sqlite:///{tmp_path / 'email-intake-authority.db'}"
    )
    engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False} if not is_postgres else {},
        future=True,
    )
    if not is_postgres:
        Base.metadata.create_all(engine)
    connection = engine.connect()
    transaction = connection.begin()
    factory = sessionmaker(
        bind=connection,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
    session = factory()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
        if not is_postgres:
            Base.metadata.drop_all(engine)
        engine.dispose()


def _ownership(tenant: Tenant) -> dict[str, object]:
    return {
        "tenant_id": tenant.id,
        "tenant_assignment_source": "fixture",
        "tenant_assignment_version": "email-intake-authority-v1",
    }


def _ticket(
    db,
    *,
    tenant: Tenant,
    market: Market,
    team: Team,
    customer: Customer,
    suffix: str,
    contact: str,
) -> Ticket:
    row = Ticket(
        ticket_no=f"EMAIL-AUTH-{suffix}-{uuid.uuid4().hex[:8]}",
        title=f"Email authority {suffix}",
        description="Same-Tenant cross-Customer authority regression",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.email,
        priority=TicketPriority.medium,
        status=TicketStatus.in_progress,
        conversation_state=ConversationState.human_owned,
        market_id=market.id,
        team_id=team.id,
        preferred_reply_channel=SourceChannel.email.value,
        preferred_reply_contact=contact,
        source_chat_id=contact,
        **_ownership(tenant),
    )
    db.add(row)
    db.flush()
    return row


class _Mailbox:
    def __init__(self, raw: bytes) -> None:
        self.raw = raw

    def select(self, _mailbox):
        return "OK", [b""]

    def uid(self, command, *_args):
        if command == "search":
            return "OK", [b"1"]
        if command == "fetch":
            return "OK", [(b"1 (RFC822)", self.raw)]
        raise AssertionError(command)

    def logout(self):
        return "BYE"


def test_same_tenant_cross_customer_ticket_hint_is_quarantined_without_mutation(
    db_session,
):
    unique = uuid.uuid4().hex[:10]
    tenant = Tenant(
        tenant_key=f"email-authority-{unique}",
        display_name="Email Authority",
        is_active=True,
    )
    db_session.add(tenant)
    db_session.flush()
    market = Market(
        code=f"EA-{unique[:6]}",
        name="Email Authority Market",
        country_code="ME",
        is_active=True,
        **_ownership(tenant),
    )
    db_session.add(market)
    db_session.flush()
    team = Team(
        name=f"Email Authority Team {unique}",
        team_type="support",
        market_id=market.id,
        is_active=True,
        **_ownership(tenant),
    )
    db_session.add(team)
    db_session.flush()
    actor = User(
        username=f"email-authority-{unique}",
        display_name="Email Authority Actor",
        email=f"operator-{unique}@example.test",
        password_hash="x",
        role="admin",
        team_id=team.id,
        is_active=True,
        **_ownership(tenant),
    )
    db_session.add(actor)
    db_session.flush()

    attacker_address = f"attacker-{unique}@example.test"
    victim_address = f"victim-{unique}@example.test"
    attacker = resolve_or_create_customer(
        db_session,
        tenant_id=tenant.id,
        identity_type="email",
        identity_value=attacker_address,
        display_name="Customer A",
        source="test",
    )
    victim = resolve_or_create_customer(
        db_session,
        tenant_id=tenant.id,
        identity_type="email",
        identity_value=victim_address,
        display_name="Customer B",
        source="test",
    )
    attacker_ticket = _ticket(
        db_session,
        tenant=tenant,
        market=market,
        team=team,
        customer=attacker,
        suffix="A",
        contact=attacker_address,
    )
    victim_ticket = _ticket(
        db_session,
        tenant=tenant,
        market=market,
        team=team,
        customer=victim,
        suffix="B",
        contact=victim_address,
    )
    account = OutboundEmailAccount(
        display_name="Authority mailbox",
        host="smtp.invalid.test",
        port=587,
        username="support",
        password_encrypted="ciphertext",
        from_address=f"support-{unique}@example.test",
        security_mode="starttls",
        is_active=True,
        priority=1,
        market_id=market.id,
        inbound_enabled=True,
        imap_host="imap.invalid.test",
        imap_port=993,
        imap_username="support",
        imap_password_encrypted="ciphertext",
        imap_security_mode="ssl",
        imap_mailbox="INBOX",
        created_by=actor.id,
        updated_by=actor.id,
    )
    db_session.add(account)
    db_session.flush()

    message = ParsedMailboxMessage(
        uid="1",
        from_address=attacker_address,
        from_name="Customer A",
        to_address=account.from_address,
        cc=None,
        subject=f"Please attach this to nexusdesk-ticket-{victim_ticket.id}",
        body=(
            f"I am Customer A but this body references "
            f"nexusdesk-ticket-{victim_ticket.id}."
        ),
        message_id=f"<attacker-{unique}@example.test>",
        references=None,
        in_reply_to=None,
        received_at=datetime.now(timezone.utc),
        raw_preview="cross customer attempt",
    )

    victim_snapshot = {
        "status": victim_ticket.status,
        "conversation_state": victim_ticket.conversation_state,
        "preferred_reply_contact": victim_ticket.preferred_reply_contact,
        "preferred_reply_channel": victim_ticket.preferred_reply_channel,
        "source_chat_id": victim_ticket.source_chat_id,
        "updated_at": victim_ticket.updated_at,
    }
    victim_bindings = (
        db_session.query(CustomerIdentityBinding)
        .filter(CustomerIdentityBinding.customer_id == victim.id)
        .count()
    )

    assert _resolve_ticket(db_session, account, message) is None

    raw = (
        f"From: Customer A <{attacker_address}>\r\n"
        f"To: {account.from_address}\r\n"
        f"Subject: {message.subject}\r\n"
        f"Message-ID: {message.message_id}\r\n"
        "Date: Mon, 27 Jul 2026 10:00:00 +0000\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n\r\n"
        f"{message.body}"
    ).encode("utf-8")
    result = poll_imap_account(
        db_session,
        account,
        client=_Mailbox(raw),
    )

    assert result.fetched == 1
    assert result.ingested == 0
    assert result.quarantined == 1
    quarantine = db_session.query(EmailIntakeQuarantine).one()
    assert quarantine.tenant_id == tenant.id
    assert quarantine.reason_code == "ticket_authority_mismatch"
    assert quarantine.status == "pending_intake"

    db_session.refresh(victim_ticket)
    assert victim_ticket.status == victim_snapshot["status"]
    assert victim_ticket.conversation_state == victim_snapshot["conversation_state"]
    assert (
        victim_ticket.preferred_reply_contact
        == victim_snapshot["preferred_reply_contact"]
    )
    assert (
        victim_ticket.preferred_reply_channel
        == victim_snapshot["preferred_reply_channel"]
    )
    assert victim_ticket.source_chat_id == victim_snapshot["source_chat_id"]
    assert victim_ticket.updated_at == victim_snapshot["updated_at"]
    assert (
        db_session.query(CustomerIdentityBinding)
        .filter(CustomerIdentityBinding.customer_id == victim.id)
        .count()
        == victim_bindings
    )
    assert (
        db_session.query(CustomerIdentityBinding)
        .filter(
            CustomerIdentityBinding.customer_id == victim.id,
            CustomerIdentityBinding.normalized_value == attacker_address,
        )
        .count()
        == 0
    )
    assert (
        db_session.query(TicketInboundEmailMessage)
        .filter(TicketInboundEmailMessage.ticket_id == victim_ticket.id)
        .count()
        == 0
    )
    assert attacker_ticket.customer_id == attacker.id
