from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Ticket, TicketEvent
from app.services import webchat_handoff_snapshot_service as svc
from app.webchat_models import WebchatMessage

pytestmark = pytest.mark.fast_lane_v2_2_2


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("CREATE UNIQUE INDEX ux_tickets_source_dedupe_key ON tickets(source_dedupe_key)"))
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_webchat_messages_conversation_client ON webchat_messages(conversation_id, client_message_id) WHERE client_message_id IS NOT NULL"))
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


def test_create_ticket_from_webchat_snapshot_sets_source_dedupe_key(db_session):
    snapshot = _snapshot()
    ticket = svc.create_ticket_from_webchat_snapshot(db_session, snapshot=snapshot)

    assert ticket.source_dedupe_key == svc.webchat_handoff_source_dedupe_key(snapshot)
    assert db_session.query(Ticket).count() == 1
    assert db_session.query(TicketEvent).count() == 1


def test_create_ticket_from_webchat_snapshot_returns_existing_after_unique_conflict(db_session, monkeypatch):
    snapshot = _snapshot()
    existing = svc.create_ticket_from_webchat_snapshot(db_session, snapshot=snapshot)
    original_lookup = svc._existing_ticket
    calls = {"count": 0}

    def fake_lookup(db, snapshot_arg: dict, source_dedupe_key: str):
        calls["count"] += 1
        if calls["count"] == 1:
            return None
        return original_lookup(db, snapshot_arg, source_dedupe_key)

    monkeypatch.setattr(svc, "_existing_ticket", fake_lookup)

    returned = svc.create_ticket_from_webchat_snapshot(db_session, snapshot=snapshot)

    assert returned.id == existing.id
    assert db_session.query(Ticket).count() == 1
    assert db_session.query(TicketEvent).count() == 1


def test_existing_ticket_message_linkage_returns_existing_after_unique_conflict(db_session, monkeypatch):
    snapshot = _snapshot()
    existing = svc.create_ticket_from_webchat_snapshot(db_session, snapshot=snapshot)
    original_find_message = svc._find_message
    calls = {"count": 0}

    def fake_find_message(db, *, conversation_id: int, client_message_id: str):
        calls["count"] += 1
        if calls["count"] == 1:
            return None
        return original_find_message(db, conversation_id=conversation_id, client_message_id=client_message_id)

    monkeypatch.setattr(svc, "_find_message", fake_find_message)

    returned = svc.create_ticket_from_webchat_snapshot(db_session, snapshot=snapshot)

    assert returned.id == existing.id
    messages = db_session.execute(select(WebchatMessage).order_by(WebchatMessage.id.asc())).scalars().all()
    assert [message.direction for message in messages] == ["visitor", "ai", "system"]
    assert len({message.client_message_id for message in messages}) == 3
    assert all(message.ticket_id == existing.id for message in messages)
