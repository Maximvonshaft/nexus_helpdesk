from __future__ import annotations

import json
import os
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_events_visitor_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.api.webchat_events import PUBLIC_VISITOR_EVENTS_ERROR, _hash_token, poll_webchat_events  # noqa: E402
from app.db import Base  # noqa: E402
from app.enums import SourceChannel, TicketPriority, TicketSource  # noqa: E402
from app.models import Ticket  # noqa: E402
from app.utils.time import utc_now  # noqa: E402
from app.webchat_models import WebchatConversation, WebchatEvent  # noqa: E402


class FakeRequest:
    headers: dict[str, str] = {}


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "webchat_events_visitor.db"
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


def make_ticket(db, ticket_no: str) -> Ticket:
    row = Ticket(
        ticket_no=ticket_no,
        title=ticket_no,
        description=ticket_no,
        source=TicketSource.manual,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
    )
    db.add(row)
    db.flush()
    return row


def make_conversation(db, ticket: Ticket, public_id: str, token: str, *, expired: bool = False) -> WebchatConversation:
    row = WebchatConversation(
        public_id=public_id,
        visitor_token_hash=_hash_token(token),
        visitor_token_expires_at=utc_now() - timedelta(minutes=1) if expired else utc_now() + timedelta(days=1),
        tenant_key="default",
        channel_key="default",
        ticket_id=ticket.id,
    )
    db.add(row)
    db.flush()
    db.add(WebchatEvent(
        conversation_id=row.id,
        ticket_id=ticket.id,
        event_type="message.created",
        payload_json=json.dumps({"conversation": public_id}),
    ))
    db.flush()
    return row


def call_public_events(db, public_id: str, token: str | None, *, after_id: int = 0, limit: int = 50):
    return poll_webchat_events(
        public_id,
        FakeRequest(),
        Response(),
        visitor_token=None,
        after_id=after_id,
        limit=limit,
        wait_ms=0,
        x_webchat_visitor_token=token,
        db=db,
    )


def test_legal_visitor_token_after_id_and_limit_returns_events(db_session):
    ticket = make_ticket(db_session, "T-VISITOR-A")
    make_conversation(db_session, ticket, "wc-a", "token-a")

    response = call_public_events(db_session, "wc-a", "token-a", after_id=0, limit=10)

    assert len(response["events"]) == 1
    assert response["events"][0]["payload_json"]["conversation"] == "wc-a"
    assert response["has_more"] is False


def test_visitor_a_token_cannot_read_visitor_b_events(db_session):
    ticket_a = make_ticket(db_session, "T-VISITOR-A2")
    ticket_b = make_ticket(db_session, "T-VISITOR-B2")
    make_conversation(db_session, ticket_a, "wc-a2", "token-a")
    make_conversation(db_session, ticket_b, "wc-b2", "token-b")

    with pytest.raises(HTTPException) as exc:
        call_public_events(db_session, "wc-b2", "token-a")

    assert exc.value.status_code == 404
    assert exc.value.detail == PUBLIC_VISITOR_EVENTS_ERROR


def test_wrong_token_and_missing_conversation_share_same_response_shape(db_session):
    ticket = make_ticket(db_session, "T-VISITOR-C")
    make_conversation(db_session, ticket, "wc-c", "token-c")

    errors = []
    for public_id, token in [("wc-c", "wrong-token"), ("wc-missing", "wrong-token")]:
        with pytest.raises(HTTPException) as exc:
            call_public_events(db_session, public_id, token)
        errors.append((exc.value.status_code, exc.value.detail))

    assert errors[0] == errors[1]
    assert errors[0] == (404, PUBLIC_VISITOR_EVENTS_ERROR)


def test_expired_token_uses_same_safe_response_shape(db_session):
    ticket = make_ticket(db_session, "T-VISITOR-EXPIRED")
    make_conversation(db_session, ticket, "wc-expired", "expired-token", expired=True)

    with pytest.raises(HTTPException) as exc:
        call_public_events(db_session, "wc-expired", "expired-token")

    assert exc.value.status_code == 404
    assert exc.value.detail == PUBLIC_VISITOR_EVENTS_ERROR


def test_legacy_query_token_transport_remains_disabled(db_session):
    ticket = make_ticket(db_session, "T-VISITOR-QUERY")
    make_conversation(db_session, ticket, "wc-query", "token-query")

    with pytest.raises(HTTPException) as exc:
        poll_webchat_events(
            "wc-query",
            FakeRequest(),
            Response(),
            visitor_token="token-query",
            after_id=0,
            limit=10,
            wait_ms=0,
            x_webchat_visitor_token=None,
            db=db_session,
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == PUBLIC_VISITOR_EVENTS_ERROR
