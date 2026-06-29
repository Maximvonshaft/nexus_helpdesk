from __future__ import annotations

import os

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_fast_edge_guards_tests.db")
os.environ.setdefault("WEBCHAT_FAST_AI_ENABLED", "false")

from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, text

from app.api import webchat_fast
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Customer, Ticket, WebchatRateLimitBucket
from app.services.webchat_fast_ai_service import WebchatFastReplyResult
from app.services.webchat_fast_idempotency_db import WebchatFastIdempotency
from app.services.webchat_fast_rate_limit import reset_webchat_fast_rate_limit_for_tests
from app.webchat_models import WebchatConversation, WebchatMessage

client = TestClient(app)


def setup_function():
    db = SessionLocal()
    try:
        db.execute(text("DROP TABLE IF EXISTS webchat_rate_limits"))
        db.commit()
    finally:
        db.close()
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        db.execute(delete(WebchatFastIdempotency))
        db.execute(delete(WebchatMessage))
        db.execute(delete(WebchatConversation))
        db.execute(delete(Ticket))
        db.execute(delete(Customer))
        db.execute(delete(WebchatRateLimitBucket))
        db.commit()
    finally:
        db.close()
    reset_webchat_fast_rate_limit_for_tests()


def _payload(client_message_id: str, *, session_id: str = "edge-session", body: str = "Hi") -> dict:
    return {
        "tenant_key": "default",
        "channel_key": "website",
        "session_id": session_id,
        "client_message_id": client_message_id,
        "body": body,
        "recent_context": [],
    }


def _ok_reply(text: str = "Hi, this is Speedy.") -> WebchatFastReplyResult:
    return WebchatFastReplyResult(
        ok=True,
        ai_generated=True,
        reply_source="provider_runtime",
        reply=text,
        intent="greeting",
        tracking_number=None,
        handoff_required=False,
        handoff_reason=None,
        recommended_agent_action=None,
        ticket_creation_queued=False,
        elapsed_ms=20,
    )


def test_closed_fast_conversation_reopens_without_public_id_collision(monkeypatch):
    async def fake_generate(**kwargs):
        return _ok_reply("First reply")

    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)

    first = client.post("/api/webchat/fast-reply", json=_payload("closed-edge-1", session_id="closed-edge-session"))
    assert first.status_code == 200

    db = SessionLocal()
    try:
        conversation = db.execute(select(WebchatConversation)).scalar_one()
        original_id = conversation.id
        original_public_id = conversation.public_id
        conversation.status = "closed"
        db.commit()
    finally:
        db.close()

    async def fake_generate_second(**kwargs):
        return _ok_reply("Second reply")

    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate_second)
    second = client.post("/api/webchat/fast-reply", json=_payload("closed-edge-2", session_id="closed-edge-session"))
    assert second.status_code == 200

    db = SessionLocal()
    try:
        rows = db.execute(select(WebchatConversation).where(WebchatConversation.fast_session_id == "closed-edge-session")).scalars().all()
        assert len(rows) == 1
        assert rows[0].id == original_id
        assert rows[0].public_id == original_public_id
        assert rows[0].status == "open"
    finally:
        db.close()


def test_long_client_message_id_persists_distinct_visitor_and_ai_messages(monkeypatch):
    async def fake_generate(**kwargs):
        return _ok_reply("Long id reply")

    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)
    long_client_message_id = "x" * 120

    response = client.post("/api/webchat/fast-reply", json=_payload(long_client_message_id, session_id="long-id-session"))
    assert response.status_code == 200

    db = SessionLocal()
    try:
        conversation = db.execute(select(WebchatConversation).where(WebchatConversation.fast_session_id == "long-id-session")).scalar_one()
        messages = db.execute(select(WebchatMessage).where(WebchatMessage.conversation_id == conversation.id).order_by(WebchatMessage.id.asc())).scalars().all()
        assert [message.direction for message in messages] == ["visitor", "ai"]
        assert messages[0].client_message_id == long_client_message_id
        assert messages[1].client_message_id != long_client_message_id
        assert messages[1].client_message_id is not None
        assert ":ai:" in messages[1].client_message_id
        assert len(messages[1].client_message_id) <= 120
        assert db.execute(select(func.count(WebchatMessage.id))).scalar_one() == 2
    finally:
        db.close()
