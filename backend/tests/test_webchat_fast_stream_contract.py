from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import webchat_fast
from app.main import app
from app.services.webchat_fast_stream_service import StreamBeginOutcome, sse_event

pytestmark = pytest.mark.fast_lane_v2_2_2

client = TestClient(app)


def _payload(client_message_id: str = "client-stream-1") -> dict:
    return {
        "tenant_key": "default",
        "channel_key": "website",
        "session_id": "session-stream-1",
        "client_message_id": client_message_id,
        "body": "Hi",
        "recent_context": [],
    }


def _settings(enabled: bool = True):
    return SimpleNamespace(
        stream_enabled=enabled,
        stream_require_accept=True,
        openclaw_responses_agent_id="webchat-fast",
    )


def test_stream_feature_flag_disabled(monkeypatch):
    monkeypatch.setattr(webchat_fast, "get_webchat_fast_settings", lambda: _settings(False))
    monkeypatch.setattr(webchat_fast, "enforce_webchat_fast_rate_limit", lambda *a, **k: None)
    response = client.post(
        "/api/webchat/fast-reply/stream",
        json=_payload(),
        headers={"Accept": "text/event-stream"},
    )
    assert response.status_code == 503
    assert response.json()["error_code"] == "stream_disabled"


def test_successful_stream_contract(monkeypatch):
    async def fake_stream(**kwargs):
        yield sse_event("meta", {"replayed": False})
        yield sse_event("reply_delta", {"text": "Hello"})
        yield sse_event("final", {"intent": "greeting", "handoff_required": False, "ticket_creation_queued": False})

    monkeypatch.setattr(webchat_fast, "get_webchat_fast_settings", lambda: _settings(True))
    monkeypatch.setattr(webchat_fast, "enforce_webchat_fast_rate_limit", lambda *a, **k: None)
    monkeypatch.setattr(
        webchat_fast,
        "prepare_webchat_fast_stream",
        lambda **kwargs: StreamBeginOutcome(status="owner", request_hash="h", row_id=1),
    )
    monkeypatch.setattr(webchat_fast, "stream_webchat_fast_reply_events", fake_stream)

    response = client.post(
        "/api/webchat/fast-reply/stream",
        json=_payload(),
        headers={"Accept": "text/event-stream"},
    )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    body = response.text
    assert "event: meta" in body
    assert "event: reply_delta" in body
    assert '"text":"Hello"' in body
    assert "event: final" in body
    assert '"intent":"greeting"' in body
    assert '"reply"' not in body.split("event: final", 1)[1]
    assert '{"reply"' not in body


def test_stream_error_contract(monkeypatch):
    async def fake_stream(**kwargs):
        yield sse_event("meta", {"replayed": False})
        yield sse_event("error", {"error_code": "ai_invalid_output", "retry_after_ms": 1500})

    monkeypatch.setattr(webchat_fast, "get_webchat_fast_settings", lambda: _settings(True))
    monkeypatch.setattr(webchat_fast, "enforce_webchat_fast_rate_limit", lambda *a, **k: None)
    monkeypatch.setattr(
        webchat_fast,
        "prepare_webchat_fast_stream",
        lambda **kwargs: StreamBeginOutcome(status="owner", request_hash="h", row_id=1),
    )
    monkeypatch.setattr(webchat_fast, "stream_webchat_fast_reply_events", fake_stream)

    response = client.post(
        "/api/webchat/fast-reply/stream",
        json=_payload("client-stream-error"),
        headers={"Accept": "text/event-stream"},
    )
    assert response.status_code == 200
    assert "event: error" in response.text
    assert "ai_invalid_output" in response.text
    assert "OpenClaw" not in response.text


def test_active_processing_returns_202_before_streaming(monkeypatch):
    calls = {"stream": 0}

    async def fake_stream(**kwargs):
        calls["stream"] += 1
        yield sse_event("reply_delta", {"text": "should not run"})

    monkeypatch.setattr(webchat_fast, "get_webchat_fast_settings", lambda: _settings(True))
    monkeypatch.setattr(webchat_fast, "enforce_webchat_fast_rate_limit", lambda *a, **k: None)
    monkeypatch.setattr(
        webchat_fast,
        "prepare_webchat_fast_stream",
        lambda **kwargs: StreamBeginOutcome(status="processing", request_hash="h", error_code="request_processing"),
    )
    monkeypatch.setattr(webchat_fast, "stream_webchat_fast_reply_events", fake_stream)

    response = client.post(
        "/api/webchat/fast-reply/stream",
        json=_payload("client-processing"),
        headers={"Accept": "text/event-stream"},
    )
    assert response.status_code == 202
    assert response.json()["error_code"] == "request_processing"
    assert calls["stream"] == 0
