from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/ticket_summary_evidence_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.api.ticket_perf import get_ticket_summary  # noqa: E402
from app.db import Base  # noqa: E402
from app.enums import NoteVisibility, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole  # noqa: E402
from app.models import (  # noqa: E402
    Customer,
    Market,
    MarketBulletin,
    OpenClawAttachmentReference,
    OpenClawConversationLink,
    OpenClawTranscriptMessage,
    Team,
    Ticket,
    TicketAttachment,
    User,
)


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "ticket_summary_evidence.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False}, future=True)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def seed_summary_case(db):
    market = Market(code="CH", name="Switzerland", country_code="CH")
    team = Team(name="support", market=market)
    admin = User(
        username="admin",
        display_name="Admin",
        email="admin@invalid.test",
        password_hash="x",
        role=UserRole.admin,
        team=team,
        is_active=True,
    )
    customer = Customer(name="Customer", email="customer@invalid.test")
    db.add_all([market, team, admin, customer])
    db.flush()
    ticket = Ticket(
        ticket_no="SUM-001",
        title="Summary evidence case",
        description="Evidence should be truthful",
        customer_id=customer.id,
        source=TicketSource.manual,
        source_channel=SourceChannel.email,
        priority=TicketPriority.high,
        status=TicketStatus.in_progress,
        assignee_id=admin.id,
        team_id=team.id,
        market_id=market.id,
        country_code="CH",
    )
    db.add(ticket)
    db.flush()
    attachment = TicketAttachment(
        ticket_id=ticket.id,
        uploaded_by=admin.id,
        file_name="pod.jpg",
        file_url="https://example.invalid/pod.jpg",
        mime_type="image/jpeg",
        file_size=1234,
        visibility=NoteVisibility.external,
    )
    conversation = OpenClawConversationLink(ticket_id=ticket.id, session_key="session-1", channel="webchat", recipient="visitor")
    db.add_all([attachment, conversation])
    db.flush()
    transcript = OpenClawTranscriptMessage(
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        session_key="session-1",
        message_id="m-1",
        role="visitor",
        author_name="Visitor",
        body_text="Where is my parcel?",
        received_at=datetime(2026, 5, 20, 8, 0, 0),
    )
    db.add(transcript)
    db.flush()
    ref = OpenClawAttachmentReference(
        ticket_id=ticket.id,
        conversation_id=conversation.id,
        transcript_message_id=transcript.id,
        remote_attachment_id="remote-1",
        content_type="image/jpeg",
        filename="chat-proof.jpg",
        storage_status="referenced",
    )
    bulletin = MarketBulletin(
        market_id=market.id,
        country_code="CH",
        title="Zurich delay notice",
        body="Use the approved Zurich delay wording.",
        summary="Zurich delay",
        category="delay",
        audience="customer",
        severity="warning",
        is_active=True,
        auto_inject_to_ai=True,
    )
    db.add_all([ref, bulletin])
    db.commit()
    return admin, ticket


def test_ticket_summary_returns_truthful_evidence_previews(db_session):
    admin, ticket = seed_summary_case(db_session)

    payload = get_ticket_summary(ticket.id, db=db_session, current_user=admin)

    assert payload["attachments_count"] == 1
    assert payload["openclaw_transcript_count"] == 1
    assert payload["openclaw_attachment_references_count"] == 1
    assert payload["active_market_bulletins_count"] == 1
    assert payload["evidence_summary"]["loaded"] is True
    assert payload["evidence_summary"]["attachments_count"] == 1
    assert payload["evidence_summary"]["openclaw_transcript_count"] == 1
    assert payload["evidence_summary"]["openclaw_attachment_references_count"] == 1
    assert payload["evidence_summary"]["active_market_bulletins_count"] == 1
    assert payload["attachments"][0]["file_name"] == "pod.jpg"
    assert payload["openclaw_transcript"][0]["body_text"] == "Where is my parcel?"
    assert payload["openclaw_attachment_references"][0]["filename"] == "chat-proof.jpg"
    assert payload["active_market_bulletins"][0]["title"] == "Zurich delay notice"
