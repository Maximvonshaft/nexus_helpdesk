from __future__ import annotations

import os
from datetime import timedelta
from types import SimpleNamespace

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_stream_replay_semantics.db")
os.environ.setdefault("WEBCHAT_FAST_AI_ENABLED", "false")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.api import webchat_fast
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Customer, Ticket
from app.services.webchat_fast_ai_service import WebchatFastReplyResult
from app.services.webchat_fast_idempotency_db import WebchatFastIdempotency, compute_request_hash
from app.utils.time import utc_now
from app.webchat_models import WebchatConversation, WebchatHandoffRequest, WebchatMessage

pytestmark = pytest.mark.fast_lane_v2_2_2

client = TestClient(app)


def setup_function():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        db.execute(delete(WebchatFastIdempotency))
        db.execute(delete(WebchatHandoffRequest))
        db.execute(delete(WebchatMessage))
        db.execute(delete(WebchatConversation))
        db.execute(delete(Ticket))
        db.execute(delete(Customer))
        db.commit()
    finally:
        db.close()


def _settings(enabled: bool = True):
    return SimpleNamespace(
        stream_enabled=enabled,
        stream_require_accept=True,
        provider_runtime_agent_id="webchat-fast",
        is_openclaw_stream_configured=True,
    )


def _payload(client_message_id: str = "client-replay-1") -> dict:
    return {
        "tenant_key": "default",
        "channel_key": "website",
        "session_id": "session-replay-1",
        "client_message_id": client_message_id,
        "body": "Hi",
        "recent_context": [],
    }


def _ok_reply() -> WebchatFastReplyResult:
    return WebchatFastReplyResult(
        ok=True,
        ai_generated=True,
        reply_source="provider_runtime",
        reply="Hello",
        intent="general_support",
        tracking_number=None,
        handoff_required=False,
        handoff_reason=None,
        recommended_agent_action=None,
        ticket_creation_queued=False,
        elapsed_ms=20,
    )


def test_active_processing_row_returns_202_and_does_not_call_ai(monkeypatch):
    payload = _payload("client-active")
    request_hash = compute_request_hash(
        tenant_key=payload["tenant_key"],
        channel_key=payload["channel_key"],
        session_id=payload["session_id"],
        client_message_id=payload["client_message_id"],
        body=payload["body"],
        recent_context=payload["recent_context"],
    )
    db = SessionLocal()
    try:
        row = WebchatFastIdempotency(
            tenant_key=payload["tenant_key"],
            session_id=payload["session_id"],
            client_message_id=payload["client_message_id"],
            request_hash=request_hash,
            status="processing",
            locked_until=utc_now() + timedelta(seconds=60),
            owner_request_id="existing-owner",
            attempt_count=1,
            expires_at=utc_now() + timedelta(minutes=10),
        )
        db.add(row)
        db.commit()
    finally:
        db.close()

    calls = {"count": 0}

    async def fake_generate(**kwargs):
        calls["count"] += 1
        return _ok_reply()

    monkeypatch.setattr(webchat_fast, "get_webchat_fast_settings", lambda: _settings(True))
    monkeypatch.setattr(webchat_fast, "enforce_webchat_fast_rate_limit", lambda *a, **k: None)
    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)

    response = client.post("/api/webchat/fast-reply/stream", json=payload, headers={"Accept": "text/event-stream"})

    assert response.status_code == 202
    assert response.json()["error_code"] == "request_processing"
    assert calls["count"] == 0


def test_done_replay_emits_replay_event_and_final_replayed_true(monkeypatch):
    calls = {"count": 0}

    async def fake_generate(**kwargs):
        calls["count"] += 1
        return _ok_reply()

    monkeypatch.setattr(webchat_fast, "get_webchat_fast_settings", lambda: _settings(True))
    monkeypatch.setattr(webchat_fast, "enforce_webchat_fast_rate_limit", lambda *a, **k: None)
    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)

    first = client.post("/api/webchat/fast-reply/stream", json=_payload("client-replay-done"), headers={"Accept": "text/event-stream"})
    second = client.post("/api/webchat/fast-reply/stream", json=_payload("client-replay-done"), headers={"Accept": "text/event-stream"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert "event: replay" in second.text
    assert "Hello" in second.text
    assert '"replayed":true' in second.text
    assert calls["count"] == 1
