from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_stream_feature_flag.db")
os.environ.setdefault("WEBCHAT_FAST_AI_ENABLED", "false")

from app.api import webchat_fast
from app.db import Base, SessionLocal, engine
from app.main import app as fastapi_app
from app.models import Customer, Ticket
from app.services.webchat_fast_ai_service import WebchatFastReplyResult
from app.services.webchat_fast_idempotency_db import WebchatFastIdempotency
from app.webchat_models import WebchatConversation, WebchatHandoffRequest, WebchatMessage

pytestmark = pytest.mark.fast_lane_v2_2_2

client = TestClient(fastapi_app)


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(engine)
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
    yield


def _payload(client_message_id: str = "stream-flag-1") -> dict:
    return {
        "tenant_key": "default",
        "channel_key": "website",
        "session_id": "session-stream-flag",
        "client_message_id": client_message_id,
        "body": "Hi",
        "recent_context": [],
    }


def _settings(*, enabled: bool = True, require_accept: bool = True):
    return SimpleNamespace(
        stream_enabled=enabled,
        stream_require_accept=require_accept,
        provider_runtime_agent_id="webchat-fast",
        is_external_channel_stream_configured=True,
    )


def _parse_sse(body: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        event = "message"
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].lstrip())
        if data_lines:
            events.append((event, json.loads("\n".join(data_lines))))
    return events


def _ok_reply(text: str = "Hello") -> WebchatFastReplyResult:
    return WebchatFastReplyResult(
        ok=True,
        ai_generated=True,
        reply_source="provider_runtime",
        reply=text,
        intent="general_support",
        tracking_number=None,
        handoff_required=False,
        handoff_reason=None,
        recommended_agent_action=None,
        ticket_creation_queued=False,
        elapsed_ms=10,
    )


def test_stream_disabled_env_blocks_stream_but_non_stream_still_works(monkeypatch):
    monkeypatch.setattr(webchat_fast, "get_webchat_fast_settings", lambda: _settings(enabled=False))
    monkeypatch.setattr(webchat_fast, "enforce_webchat_fast_rate_limit", lambda *a, **k: None)

    async def fake_generate(**kwargs):
        return _ok_reply()

    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)

    stream = client.post("/api/webchat/fast-reply/stream", json=_payload("stream-off"), headers={"Accept": "text/event-stream"})
    nonstream = client.post("/api/webchat/fast-reply", json=_payload("stream-off-nonstream"))

    assert stream.status_code == 503
    assert stream.json()["error_code"] == "stream_disabled"
    assert nonstream.status_code == 200
    assert nonstream.json()["reply"] == "Hello"
    assert nonstream.json()["ai_decision_trace"]["schema_version"] == "webchat_ai_decision_v1"


def test_stream_accept_header_is_required(monkeypatch):
    monkeypatch.setattr(webchat_fast, "get_webchat_fast_settings", lambda: _settings(enabled=True, require_accept=True))
    monkeypatch.setattr(webchat_fast, "enforce_webchat_fast_rate_limit", lambda *a, **k: None)

    response = client.post("/api/webchat/fast-reply/stream", json=_payload("stream-accept-required"))

    assert response.status_code == 406
    assert response.json()["error_code"] == "stream_accept_required"


def test_stream_enabled_env_allows_decision_runtime_path(monkeypatch):
    async def fake_generate(**kwargs):
        assert kwargs["body"] == "Hi"
        return _ok_reply("Hello from stream")

    monkeypatch.setattr(webchat_fast, "get_webchat_fast_settings", lambda: _settings(enabled=True))
    monkeypatch.setattr(webchat_fast, "enforce_webchat_fast_rate_limit", lambda *a, **k: None)
    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)

    response = client.post("/api/webchat/fast-reply/stream", json=_payload("stream-on"), headers={"Accept": "text/event-stream"})
    events = _parse_sse(response.text)
    final = [payload for event, payload in events if event == "final"][0]

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert [event for event, _payload in events] == ["meta", "final", "reply_delta"]
    assert final["reply_source"] == "provider_runtime"
    assert final["ai_decision_trace"]["policy_gate"]["ok"] is True
    assert [payload for event, payload in events if event == "reply_delta"][0]["text"] == "Hello from stream"


def test_stream_idempotency_replays_cached_decision(monkeypatch):
    calls = {"ai": 0}

    async def fake_generate(**kwargs):
        calls["ai"] += 1
        return _ok_reply("Hello once")

    monkeypatch.setattr(webchat_fast, "get_webchat_fast_settings", lambda: _settings(enabled=True))
    monkeypatch.setattr(webchat_fast, "enforce_webchat_fast_rate_limit", lambda *a, **k: None)
    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)

    payload = _payload("stream-idempotent")
    first = client.post("/api/webchat/fast-reply/stream", json=payload, headers={"Accept": "text/event-stream"})
    second = client.post("/api/webchat/fast-reply/stream", json=payload, headers={"Accept": "text/event-stream"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert '"replayed":true' in second.text
    assert calls == {"ai": 1}
    db = SessionLocal()
    try:
        row = db.execute(select(WebchatFastIdempotency).where(WebchatFastIdempotency.client_message_id == "stream-idempotent")).scalar_one()
        assert row.status == "done"
        assert row.response_json["ai_decision_trace"]["policy_gate"]["ok"] is True
    finally:
        db.close()
