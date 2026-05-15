from __future__ import annotations

import json
import os

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_fast_handoff_linkage.db")

from sqlalchemy import delete, select

from app.db import Base, SessionLocal, engine
from app.models import BackgroundJob, Customer, Ticket, TicketEvent
from app.services.webchat_handoff_snapshot_service import build_handoff_snapshot_payload, create_ticket_from_webchat_snapshot, process_webchat_handoff_snapshot_job
from app.webchat_models import WebchatConversation, WebchatMessage


def setup_function():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        for model in (WebchatMessage, WebchatConversation, TicketEvent, BackgroundJob, Ticket, Customer):
            db.execute(delete(model))
        db.commit()
    finally:
        db.close()


def _snapshot() -> dict:
    return build_handoff_snapshot_payload(
        tenant_key="default",
        channel_key="website",
        session_id="fast-session-1",
        client_message_id="fast-client-1",
        customer_last_message="I want compensation for my missing parcel.",
        ai_reply="A human teammate will review this request.",
        intent="handoff",
        tracking_number="SF123456789",
        handoff_reason="refund_or_compensation_requires_human_review",
        recommended_agent_action="Review claim and verify shipment evidence.",
        recent_context=[{"role": "customer", "text": "Where is my parcel?"}],
        visitor={"name": "Alice", "email": "alice@example.test", "phone": "+41123456789"},
    )


def test_fast_handoff_creates_ticket_customer_conversation_and_messages():
    db = SessionLocal()
    try:
        ticket = create_ticket_from_webchat_snapshot(db, snapshot=_snapshot())
        db.commit()

        assert ticket.customer_id is not None
        assert ticket.preferred_reply_channel == "web_chat"
        assert ticket.preferred_reply_contact.startswith("wcf_")
        assert ticket.source_chat_id == f"webchat-fast:{ticket.preferred_reply_contact}"[:120]

        customer = db.get(Customer, ticket.customer_id)
        assert customer is not None
        assert customer.email_normalized == "alice@example.test"

        conversation = db.execute(select(WebchatConversation).where(WebchatConversation.ticket_id == ticket.id)).scalar_one()
        assert conversation.public_id == ticket.preferred_reply_contact
        assert conversation.tenant_key == "default"
        assert conversation.channel_key == "website"

        messages = db.execute(select(WebchatMessage).where(WebchatMessage.conversation_id == conversation.id).order_by(WebchatMessage.id.asc())).scalars().all()
        assert [m.direction for m in messages] == ["visitor", "ai", "system"]
        assert messages[0].body == "I want compensation for my missing parcel."
        assert messages[0].client_message_id == "fast-client-1"
        assert messages[1].body == "A human teammate will review this request."
        assert messages[1].client_message_id == "fast-client-1:ai"
        assert messages[2].client_message_id == "fast-client-1:handoff"

        event = db.execute(select(TicketEvent).where(TicketEvent.ticket_id == ticket.id)).scalar_one()
        payload = json.loads(event.payload_json)
        assert payload["public_conversation_id"] == conversation.public_id
        assert payload["customer_id"] == ticket.customer_id
    finally:
        db.close()


def test_fast_handoff_linkage_is_idempotent_for_same_snapshot():
    snapshot = _snapshot()
    db = SessionLocal()
    try:
        first = create_ticket_from_webchat_snapshot(db, snapshot=snapshot)
        second = create_ticket_from_webchat_snapshot(db, snapshot=snapshot)
        db.commit()

        assert first.id == second.id
        assert db.execute(select(Ticket)).scalars().all() == [first]
        conversations = db.execute(select(WebchatConversation)).scalars().all()
        messages = db.execute(select(WebchatMessage)).scalars().all()
        assert len(conversations) == 1
        assert len(messages) == 3
    finally:
        db.close()


def test_handoff_worker_result_returns_public_conversation_id():
    db = SessionLocal()
    try:
        result = process_webchat_handoff_snapshot_job(db, snapshot=_snapshot())
        db.commit()

        assert result["status"] == "done"
        assert result["ticket_id"]
        assert result["ticket_no"]
        assert result["public_conversation_id"].startswith("wcf_")
    finally:
        db.close()
