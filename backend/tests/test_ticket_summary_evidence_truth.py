from __future__ import annotations

import os
import sys
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
from app.enums import EventType, NoteVisibility, SourceChannel, TicketPriority, TicketSource, TicketStatus, UserRole  # noqa: E402
from app.models import Customer, Market, MarketBulletin, Team, Ticket, TicketAttachment, TicketEvent, User  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'ticket_summary_evidence.db'}", connect_args={"check_same_thread": False}, future=True)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_ticket_summary_returns_canonical_evidence(db_session):
    market = Market(code="CH", name="Switzerland", country_code="CH")
    team = Team(name="support", market=market)
    admin = User(username="admin", display_name="Admin", email="admin@invalid.test", password_hash="x", role=UserRole.admin, team=team, is_active=True)
    customer = Customer(name="Customer", email="customer@invalid.test")
    db_session.add_all([market, team, admin, customer]); db_session.flush()
    ticket = Ticket(ticket_no="SUM-001", title="Summary evidence case", description="Evidence should be truthful", customer_id=customer.id, source=TicketSource.manual, source_channel=SourceChannel.email, priority=TicketPriority.high, status=TicketStatus.in_progress, assignee_id=admin.id, team_id=team.id, market_id=market.id, country_code="CH")
    db_session.add(ticket); db_session.flush()
    db_session.add_all([
        TicketAttachment(ticket_id=ticket.id, uploaded_by=admin.id, file_name="pod.jpg", file_url="https://example.invalid/pod.jpg", mime_type="image/jpeg", file_size=1234, visibility=NoteVisibility.external),
        TicketEvent(ticket_id=ticket.id, actor_id=admin.id, event_type=EventType.field_updated, field_name="retired_source_message", note="Historical message preserved", payload_json='{"body_text":"Where is my parcel?"}'),
        MarketBulletin(market_id=market.id, country_code="CH", title="Zurich delay notice", body="Use approved wording.", summary="Zurich delay", category="delay", audience="customer", severity="warning", is_active=True, auto_inject_to_ai=True),
    ])
    db_session.commit()

    payload = get_ticket_summary(ticket.id, db=db_session, current_user=admin)
    assert payload["attachments_count"] == 1
    assert payload["events_count"] == 1
    assert payload["active_market_bulletins_count"] == 1
    assert payload["evidence_summary"]["attachments_count"] == 1
    assert payload["evidence_summary"]["events_count"] == 1
    assert payload["attachments"][0]["file_name"] == "pod.jpg"
    assert payload["active_market_bulletins"][0]["title"] == "Zurich delay notice"
