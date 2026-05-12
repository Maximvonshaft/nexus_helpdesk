from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Ticket, TicketEvent
from app.services import webchat_handoff_snapshot_service as svc

pytestmark = pytest.mark.fast_lane_v2_2_2


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("CREATE UNIQUE INDEX ux_tickets_source_dedupe_key ON tickets(source_dedupe_key)"))
    Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _snapshot(client_message_id: str = "client-1") -> dict:
    return svc.build_handoff_snapshot_payload(
        tenant_key="default",
        channel_key="website",
        session_id="session-1",
        client_message_id=client_message_id,
        customer_last_message="Need help",
        ai_reply="I’ll hand this over.",
        intent="handoff",
        tracking_number=None,
        handoff_reason="manual_review_required",
        recommended_agent_action="Review the request.",
        recent_context=[],
        visitor={"email": "test@example.com"},
    )


def test_create_ticket_from_webchat_snapshot_sets_orm_source_dedupe_key(db_session):
    ticket = svc.create_ticket_from_webchat_snapshot(db_session, snapshot=_snapshot())

    assert ticket.source_dedupe_key == "webchat-fast-handoff:default:session-1:client-1"
    assert db_session.query(Ticket).count() == 1
    assert db_session.query(TicketEvent).count() == 1


def test_create_ticket_from_webchat_snapshot_returns_existing_after_unique_conflict(db_session, monkeypatch):
    existing = svc.create_ticket_from_webchat_snapshot(db_session, snapshot=_snapshot())
    original_lookup = svc._existing_ticket_by_source_dedupe_key
    calls = {"count": 0}

    def fake_lookup(db, source_dedupe_key: str):
        calls["count"] += 1
        if calls["count"] == 1:
            return None
        return original_lookup(db, source_dedupe_key)

    monkeypatch.setattr(svc, "_existing_ticket_by_source_dedupe_key", fake_lookup)

    returned = svc.create_ticket_from_webchat_snapshot(db_session, snapshot=_snapshot())

    assert returned.id == existing.id
    assert db_session.query(Ticket).count() == 1
    assert db_session.query(TicketEvent).count() == 1

