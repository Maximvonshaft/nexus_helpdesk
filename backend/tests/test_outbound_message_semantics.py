from __future__ import annotations

import os
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/helpdesk_suite_outbound_semantics.db",
)
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.auth_service import hash_password  # noqa: E402
from app.db import Base  # noqa: E402
from app.enums import (  # noqa: E402
    MessageStatus,
    ResolutionCategory,
    SourceChannel,
    TicketPriority,
    TicketSource,
    TicketStatus,
    UserRole,
)
from app.models import Team, Ticket, TicketOutboundMessage, User  # noqa: E402
from app.services import message_dispatch  # noqa: E402
from app.services.message_dispatch import (  # noqa: E402
    claim_pending_messages,
    process_outbound_message,
    requeue_dead_outbound_message,
)
from app.services.outbound_semantics import (  # noqa: E402
    count_outbound_semantics,
    outbound_ui_label,
)
from app.services.timeline_service import serialize_outbound  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'outbound_semantics.db'}",
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


def _uid() -> str:
    return uuid.uuid4().hex[:10]


def _ticket(
    db_session,
    *,
    channel: SourceChannel,
    contact: str,
) -> Ticket:
    suffix = _uid()
    team = Team(name=f"Ops-{suffix}", team_type="support")
    db_session.add(team)
    db_session.flush()
    db_session.add(
        User(
            username=f"ops-{suffix}",
            display_name="Ops User",
            email=f"ops-{suffix}@example.com",
            password_hash=hash_password("pass123"),
            role=UserRole.lead,
            team_id=team.id,
            is_active=True,
        )
    )
    ticket = Ticket(
        ticket_no=f"T-{channel.value}-{suffix}",
        title="Customer message",
        description="Customer message",
        source=TicketSource.user_message,
        source_channel=channel,
        priority=TicketPriority.medium,
        status=TicketStatus.pending_assignment,
        resolution_category=ResolutionCategory.none,
        team_id=team.id,
        source_chat_id=contact,
        preferred_reply_channel=channel.value,
        preferred_reply_contact=contact,
    )
    db_session.add(ticket)
    db_session.flush()
    return ticket


def _outbound(
    db_session,
    ticket: Ticket,
    *,
    channel: SourceChannel,
    status: MessageStatus,
    provider_status: str,
) -> TicketOutboundMessage:
    row = TicketOutboundMessage(
        ticket_id=ticket.id,
        channel=channel,
        status=status,
        body="hello",
        provider_status=provider_status,
        max_retries=3,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_webchat_local_messages_do_not_count_as_provider_pending(db_session):
    ticket = _ticket(
        db_session,
        channel=SourceChannel.web_chat,
        contact="wc-local",
    )
    _outbound(
        db_session,
        ticket,
        channel=SourceChannel.web_chat,
        status=MessageStatus.sent,
        provider_status="webchat_delivered",
    )
    _outbound(
        db_session,
        ticket,
        channel=SourceChannel.web_chat,
        status=MessageStatus.sent,
        provider_status="webchat_ai_delivered",
    )
    _outbound(
        db_session,
        ticket,
        channel=SourceChannel.web_chat,
        status=MessageStatus.pending,
        provider_status="queued",
    )
    counts = count_outbound_semantics(db_session)
    assert counts["external_pending_outbound"] == 0
    assert counts["external_dead_outbound"] == 0
    assert counts["webchat_local_ack_sent"] == 1
    assert counts["webchat_ai_delivered_sent"] == 1


def test_provider_pending_counts_are_semantic(db_session):
    whatsapp = _ticket(
        db_session,
        channel=SourceChannel.whatsapp,
        contact="+15550101",
    )
    telegram = _ticket(
        db_session,
        channel=SourceChannel.telegram,
        contact="telegram:42",
    )
    sms = _ticket(
        db_session,
        channel=SourceChannel.sms,
        contact="+15550102",
    )
    _outbound(
        db_session,
        whatsapp,
        channel=SourceChannel.whatsapp,
        status=MessageStatus.pending,
        provider_status="queued",
    )
    _outbound(
        db_session,
        telegram,
        channel=SourceChannel.telegram,
        status=MessageStatus.pending,
        provider_status="queued",
    )
    _outbound(
        db_session,
        sms,
        channel=SourceChannel.sms,
        status=MessageStatus.dead,
        provider_status="dead:max_retries",
    )
    counts = count_outbound_semantics(db_session)
    assert counts["external_pending_outbound"] == 2
    assert counts["external_dead_outbound"] == 1


def test_ui_labels_expose_business_semantics(db_session):
    ticket = _ticket(
        db_session,
        channel=SourceChannel.web_chat,
        contact="wc-label",
    )
    local = _outbound(
        db_session,
        ticket,
        channel=SourceChannel.web_chat,
        status=MessageStatus.sent,
        provider_status="webchat_delivered",
    )
    ai = _outbound(
        db_session,
        ticket,
        channel=SourceChannel.web_chat,
        status=MessageStatus.sent,
        provider_status="webchat_ai_delivered",
    )
    draft = _outbound(
        db_session,
        ticket,
        channel=SourceChannel.whatsapp,
        status=MessageStatus.draft,
        provider_status="draft",
    )
    pending = _outbound(
        db_session,
        ticket,
        channel=SourceChannel.whatsapp,
        status=MessageStatus.pending,
        provider_status="queued",
    )
    assert outbound_ui_label(local.channel, local.status, local.provider_status) == "Local WebChat ACK"
    assert outbound_ui_label(ai.channel, ai.status, ai.provider_status) == "Local WebChat AI Reply"
    assert outbound_ui_label(draft.channel, draft.status, draft.provider_status) == "Draft"
    assert outbound_ui_label(pending.channel, pending.status, pending.provider_status) == "External Send Pending"
    assert serialize_outbound(local)["payload"]["is_external_send"] is False
    assert serialize_outbound(pending)["payload"]["is_external_send"] is True


def test_claim_ignores_local_webchat_pending_rows(db_session):
    webchat_ticket = _ticket(
        db_session,
        channel=SourceChannel.web_chat,
        contact="wc-pending",
    )
    whatsapp_ticket = _ticket(
        db_session,
        channel=SourceChannel.whatsapp,
        contact="+15550103",
    )
    webchat_row = _outbound(
        db_session,
        webchat_ticket,
        channel=SourceChannel.web_chat,
        status=MessageStatus.pending,
        provider_status="queued",
    )
    whatsapp_row = _outbound(
        db_session,
        whatsapp_ticket,
        channel=SourceChannel.whatsapp,
        status=MessageStatus.pending,
        provider_status="queued",
    )
    db_session.commit()
    claimed = claim_pending_messages(db_session, worker_id="worker-test")
    assert [row.id for row in claimed] == [whatsapp_row.id]
    db_session.refresh(webchat_row)
    db_session.refresh(whatsapp_row)
    assert webchat_row.status == MessageStatus.pending
    assert whatsapp_row.status == MessageStatus.processing


def test_local_outbound_never_calls_provider_dispatch(db_session, monkeypatch):
    ticket = _ticket(
        db_session,
        channel=SourceChannel.web_chat,
        contact="wc-send-block",
    )
    row = _outbound(
        db_session,
        ticket,
        channel=SourceChannel.web_chat,
        status=MessageStatus.pending,
        provider_status="queued",
    )
    db_session.commit()
    for name in ("_dispatch_whatsapp_message", "_dispatch_email_message"):
        monkeypatch.setattr(
            message_dispatch,
            name,
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("provider path must not run for web_chat")
            ),
        )
    processed = process_outbound_message(db_session, row)
    assert processed.status == MessageStatus.dead
    assert processed.failure_code == "non_external_outbound_not_dispatchable"


def test_dead_local_message_cannot_be_requeued(db_session):
    ticket = _ticket(
        db_session,
        channel=SourceChannel.web_chat,
        contact="wc-dead",
    )
    row = _outbound(
        db_session,
        ticket,
        channel=SourceChannel.web_chat,
        status=MessageStatus.dead,
        provider_status="webchat_delivered",
    )
    db_session.commit()
    with pytest.raises(HTTPException) as exc:
        requeue_dead_outbound_message(db_session, message_id=row.id)
    assert exc.value.status_code == 400
    assert "external outbound" in exc.value.detail


def test_disabled_outbound_worker_never_claims(monkeypatch):
    from scripts import run_worker

    @contextmanager
    def dummy_db_context():
        yield SimpleNamespace()

    monkeypatch.setattr(run_worker.settings, "enable_outbound_dispatch", False)
    monkeypatch.setattr(run_worker, "db_context", dummy_db_context)
    monkeypatch.setattr(
        run_worker,
        "dispatch_pending_messages",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("outbound dispatch should not run")
        ),
    )
    monkeypatch.setattr(run_worker, "record_worker_poll", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_worker, "record_worker_result", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_worker, "log_event", lambda *args, **kwargs: None)
    assert run_worker.run_queue_once("worker-test", "outbound") == 0
