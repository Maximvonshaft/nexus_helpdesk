from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text as sql_text

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_stream_final_parse_failure.db")
os.environ.setdefault("WEBCHAT_FAST_AI_ENABLED", "false")

from app.api import webchat_fast
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import BackgroundJob, Customer, Ticket
from app.services.webchat_fast_idempotency_db import WebchatFastIdempotency
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
        db.execute(delete(BackgroundJob))
        db.execute(delete(Ticket))
        db.execute(delete(Customer))
        db.commit()
    finally:
        db.close()


def _settings():
    return SimpleNamespace(stream_enabled=True, stream_require_accept=True, openclaw_responses_agent_id="webchat-fast", is_openclaw_stream_configured=True)


def _payload(client_message_id: str = "client-invalid-final") -> dict:
    return {
        "tenant_key": "default",
        "channel_key": "website",
        "session_id": "session-invalid-final",
        "client_message_id": client_message_id,
        "body": "Hi",
        "recent_context": [],
    }


def test_decision_runtime_exception_emits_error_without_partial_or_final(monkeypatch):
    async def fail_generate(**kwargs):
        raise ValueError("invalid decision output")

    monkeypatch.setattr(webchat_fast, "get_webchat_fast_settings", _settings)
    monkeypatch.setattr(webchat_fast, "enforce_webchat_fast_rate_limit", lambda *a, **k: None)
    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fail_generate)

    response = client.post("/api/webchat/fast-reply/stream", json=_payload(), headers={"Accept": "text/event-stream"})

    assert response.status_code == 200
    assert "event: reply_delta" not in response.text
    assert "invalid decision output" not in response.text
    assert "event: error" in response.text
    assert "stream_internal_error" in response.text
    assert "event: final" not in response.text

    db = SessionLocal()
    try:
        row = db.execute(select(WebchatFastIdempotency).where(WebchatFastIdempotency.client_message_id == "client-invalid-final")).scalar_one()
        assert row.status == "failed"
        assert row.error_code == "stream_internal_error"
        assert db.execute(sql_text("select count(*) from tickets")).scalar_one() == 0
        assert db.execute(sql_text("select count(*) from background_jobs")).scalar_one() == 0
    finally:
        db.close()
