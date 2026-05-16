from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_fast_reply_api_tests.db")
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
        db.commit()
    finally:
        db.close()
    reset_webchat_fast_rate_limit_for_tests()


def _payload(client_message_id: str = "client-1", *, session_id: str = "session-1", body: str = "Hi", recent_context: list[dict] | None = None) -> dict:
    return {
        "tenant_key": "default",
        "channel_key": "website",
        "session_id": session_id,
        "client_message_id": client_message_id,
        "body": body,
        "recent_context": recent_context or [],
    }


def _ok_reply(text: str = "Hi, this is Speedy.", *, handoff: bool = False, tracking: str | None = None) -> WebchatFastReplyResult:
    return WebchatFastReplyResult(
        ok=True,
        ai_generated=True,
        reply_source="openclaw_responses",
        reply=text,
        intent="tracking_lookup" if tracking else "greeting",
        tracking_number=tracking,
        handoff_required=handoff,
        handoff_reason="manual_review_required" if handoff else None,
        recommended_agent_action="Review shipment and reply." if handoff else None,
        ticket_creation_queued=False,
        elapsed_ms=20,
    )


def test_fast_reply_same_session_reuses_conversation_and_uses_server_context(monkeypatch):
    seen_contexts: list[list[dict]] = []

    async def fake_generate(**kwargs):
        seen_contexts.append(kwargs["recent_context"])
        if kwargs["body"] == "Where is my parcel?":
            return _ok_reply("Please provide your tracking number.")
        return _ok_reply("I can see this is the tracking number for your parcel inquiry.", tracking="SPX123456789CH")

    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)

    first = client.post("/api/webchat/fast-reply", json=_payload("m1", body="Where is my parcel?"))
    second = client.post("/api/webchat/fast-reply", json=_payload("m2", body="SPX123456789CH"))

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(seen_contexts) == 2
    assert any(item["text"] == "Where is my parcel?" for item in seen_contexts[1])
    assert any("tracking number" in item["text"].lower() for item in seen_contexts[1])

    db = SessionLocal()
    try:
        conversations = db.execute(select(WebchatConversation).where(WebchatConversation.fast_session_id == "session-1")).scalars().all()
        assert len(conversations) == 1
        messages = db.execute(select(WebchatMessage).where(WebchatMessage.conversation_id == conversations[0].id)).scalars().all()
        assert len(messages) == 4
        assert [m.direction for m in messages].count("visitor") == 2
        assert [m.direction for m in messages].count("ai") == 2
    finally:
        db.close()


def test_fast_handoff_same_session_does_not_create_duplicate_ticket():
    for idx in range(3):
        response = client.post(
            "/api/webchat/fast-reply",
            json=_payload(f"lost-{idx}", body="My parcel is lost SPX123456789CH"),
        )
        assert response.status_code == 200
        assert response.json()["handoff_required"] is True

    db = SessionLocal()
    try:
        assert db.execute(select(func.count(WebchatConversation.id))).scalar_one() == 1
        assert db.execute(select(func.count(Ticket.id))).scalar_one() == 1
        conversation = db.execute(select(WebchatConversation)).scalar_one()
        assert conversation.ticket_id is not None
        assert db.execute(select(func.count(WebchatMessage.id)).where(WebchatMessage.conversation_id == conversation.id)).scalar_one() >= 5
    finally:
        db.close()


def test_fast_handoff_same_tracking_number_reuses_ticket_across_sessions():
    first = client.post("/api/webchat/fast-reply", json=_payload("a1", session_id="session-a", body="My parcel is lost SPX123456789CH"))
    second = client.post("/api/webchat/fast-reply", json=_payload("b1", session_id="session-b", body="My parcel is lost SPX123456789CH"))
    assert first.status_code == 200
    assert second.status_code == 200

    db = SessionLocal()
    try:
        assert db.execute(select(func.count(WebchatConversation.id))).scalar_one() == 2
        assert db.execute(select(func.count(Ticket.id))).scalar_one() == 1
    finally:
        db.close()


def test_fast_reply_idempotency_same_client_message_id_returns_cached_response(monkeypatch):
    calls = {"generate": 0}

    async def fake_generate(**kwargs):
        calls["generate"] += 1
        return _ok_reply("Hi, this is Speedy.")

    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)
    first = client.post("/api/webchat/fast-reply", json=_payload("same-msg"))
    second = client.post("/api/webchat/fast-reply", json=_payload("same-msg"))

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["idempotent"] is True
    assert calls["generate"] == 1
    db = SessionLocal()
    try:
        assert db.execute(select(func.count(WebchatMessage.id))).scalar_one() == 2
    finally:
        db.close()


def test_fast_reply_different_session_creates_different_conversation(monkeypatch):
    async def fake_generate(**kwargs):
        return _ok_reply("Hi, this is Speedy.")

    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)
    assert client.post("/api/webchat/fast-reply", json=_payload("m1", session_id="session-a")).status_code == 200
    assert client.post("/api/webchat/fast-reply", json=_payload("m2", session_id="session-b")).status_code == 200
    db = SessionLocal()
    try:
        assert db.execute(select(func.count(WebchatConversation.id))).scalar_one() == 2
    finally:
        db.close()


def test_demo_payload_does_not_force_empty_context():
    source = Path("backend/app/static/webchat/demo/js/app.js").read_text(encoding="utf-8")
    assert "recent_context: []" not in source
    assert "sessionStorage" in source
    assert "recentContext.slice" in source


def test_widget_persists_recent_context():
    source = Path("backend/app/static/webchat/widget.js").read_text(encoding="utf-8")
    assert "contextKey" in source
    assert "sessionStorage.setItem(contextKey" in source
    assert "recent_context: state.recentContext" in source


def test_fast_rate_limit_still_blocks_rotated_sessions(monkeypatch):
    reset_webchat_fast_rate_limit_for_tests()
    monkeypatch.setenv("WEBCHAT_FAST_RATE_LIMIT_MAX_REQUESTS", "1")
    monkeypatch.setenv("WEBCHAT_FAST_RATE_LIMIT_WINDOW_SECONDS", "60")

    async def fake_generate(**kwargs):
        return _ok_reply("Hi, this is Speedy.")

    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)
    headers = {"User-Agent": "pytest-fast-limit/1.0"}
    first = client.post("/api/webchat/fast-reply", json=_payload("rl-1", session_id="session-a"), headers=headers)
    second = client.post("/api/webchat/fast-reply", json=_payload("rl-2", session_id="session-b"), headers=headers)
    assert first.status_code == 200
    assert second.status_code == 429
    db = SessionLocal()
    try:
        assert db.execute(select(func.count(WebchatRateLimitBucket.id))).scalar_one() == 1
    finally:
        db.close()
