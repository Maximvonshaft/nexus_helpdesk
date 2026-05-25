from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/helpdesk_suite_email_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.db import Base  # noqa: E402
from app.enums import MessageStatus, ResolutionCategory, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole  # noqa: E402
from app.models import ChannelAccount, Customer, EmailChannelAccount, Team, Ticket, TicketOutboundMessage, User  # noqa: E402


def make_session(tmp_path):
    db_file = tmp_path / f"email-{uuid.uuid4().hex}.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False}, future=True)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = TestingSession()
    return engine, session


def uid() -> str:
    return uuid.uuid4().hex[:10]


def admin(db_session) -> User:
    row = User(username=f"admin-{uid()}", display_name="Admin", email=f"admin-{uid()}@example.test", password_hash="x", role=UserRole.admin, is_active=True)
    db_session.add(row)
    db_session.flush()
    return row


def ticket(db_session, *, email="alice@example.test") -> Ticket:
    team = Team(name=f"Email Ops {uid()}", team_type="support")
    customer = Customer(name="Alice", email=email, email_normalized=email.lower(), phone="+15550123456")
    db_session.add_all([team, customer])
    db_session.flush()
    row = Ticket(
        ticket_no=f"EMAIL-{uid()}",
        title="Email case",
        description="Email case",
        customer_id=customer.id,
        source=TicketSource.user_message,
        source_channel=SourceChannel.email,
        priority=TicketPriority.medium,
        status=TicketStatus.pending_assignment,
        resolution_category=ResolutionCategory.none,
        team_id=team.id,
        source_chat_id=email,
        preferred_reply_channel=SourceChannel.email.value,
        preferred_reply_contact=email,
    )
    db_session.add(row)
    db_session.flush()
    return row


def verified_email_account(db_session, *, account_id: str | None = None, from_email="support@example.test") -> EmailChannelAccount:
    channel = ChannelAccount(provider="email", account_id=account_id or f"email-{uid()}", display_name="Support Email", is_active=True, priority=10, health_status="healthy")
    db_session.add(channel)
    db_session.flush()
    row = EmailChannelAccount(channel_account_id=channel.id, from_email=from_email, from_name="Support", provider="ses", region="us-east-1", verification_status="verified", is_active=True)
    db_session.add(row)
    db_session.flush()
    return row


def email_message(db_session, ticket_row: Ticket) -> TicketOutboundMessage:
    row = TicketOutboundMessage(ticket_id=ticket_row.id, channel=SourceChannel.email, status=MessageStatus.processing, body="hello", provider_status="queued", max_retries=3)
    db_session.add(row)
    db_session.flush()
    row.ticket = ticket_row
    return row
