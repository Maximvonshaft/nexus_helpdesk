from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/ticket_detail_summary_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.api.ticket_perf import get_ticket_summary  # noqa: E402
from app.db import Base  # noqa: E402
from app.enums import EventType, MessageStatus, NoteVisibility, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole  # noqa: E402
from app.models import Customer, Ticket, TicketAIIntake, TicketAttachment, TicketComment, TicketEvent, TicketInternalNote, TicketOutboundMessage, User  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "ticket_summary.db"
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


def make_admin(db):
    user = User(username="admin", display_name="Admin", email="admin@example.test", password_hash="x", role=UserRole.admin, is_active=True)
    db.add(user)
    db.flush()
    return user


def make_ticket(db, admin):
    customer = Customer(name="Customer", email="customer@example.test", phone="+410000", external_ref="ext-1")
    db.add(customer)
    db.flush()
    ticket = Ticket(
        ticket_no="CS-TEST-1",
        title="Large ticket",
        description="Large ticket description",
        customer_id=customer.id,
        source=TicketSource.manual,
        source_channel=SourceChannel.email,
        priority=TicketPriority.medium,
        status=TicketStatus.in_progress,
        created_by=admin.id,
        assignee_id=admin.id,
        last_customer_message="latest customer message",
        required_action="review",
    )
    db.add(ticket)
    db.flush()
    return ticket


def test_ticket_summary_counts_and_omits_heavy_collections(db_session):
    admin = make_admin(db_session)
    ticket = make_ticket(db_session, admin)
    for idx in range(20):
        db_session.add(TicketComment(ticket_id=ticket.id, author_id=admin.id, body=f"comment {idx}", visibility=NoteVisibility.external))
    for idx in range(10):
        db_session.add(TicketInternalNote(ticket_id=ticket.id, author_id=admin.id, body=f"note {idx}"))
    for idx in range(5):
        db_session.add(TicketAttachment(ticket_id=ticket.id, uploaded_by=admin.id, file_name=f"file{idx}.txt", mime_type="text/plain", file_size=1, visibility=NoteVisibility.external))
    for idx in range(7):
        db_session.add(TicketOutboundMessage(ticket_id=ticket.id, channel=SourceChannel.email, status=MessageStatus.sent, body=f"outbound {idx}", created_by=admin.id))
    for idx in range(3):
        db_session.add(TicketAIIntake(ticket_id=ticket.id, summary=f"ai {idx}", classification="parcel", confidence=0.9, created_by=admin.id))
    for idx in range(4):
        db_session.add(TicketEvent(ticket_id=ticket.id, actor_id=admin.id, event_type=EventType.field_updated, note=f"event {idx}"))
    db_session.commit()

    payload = get_ticket_summary(ticket.id, db=db_session, current_user=admin)

    assert payload["id"] == ticket.id
    assert payload["counts"]["comments_count"] == 20
    assert payload["counts"]["internal_notes_count"] == 10
    assert payload["counts"]["attachments_count"] == 5
    assert payload["counts"]["outbound_messages_count"] == 7
    assert payload["counts"]["ai_intakes_count"] == 3
    assert payload["counts"]["events_count"] == 4
    assert "comments" not in payload
    assert "internal_notes" not in payload
    assert "outbound_messages" not in payload
    assert "ai_intakes" not in payload
    assert payload["attachments"] == []
    assert payload["openclaw_transcript"] == []
