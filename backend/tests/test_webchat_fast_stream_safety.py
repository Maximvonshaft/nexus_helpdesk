from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_fast_stream_safety.db")
os.environ.setdefault("WEBCHAT_FAST_AI_ENABLED", "false")

from app.api import webchat_fast
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import BackgroundJob, Customer, Ticket
from app.services.tracking_fact_schema import TrackingFactResult
from app.services.webchat_fast_ai_service import WebchatFastReplyResult
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


def _payload(client_message_id: str = "stream-safety-1", *, body: str = "Hi") -> dict:
    return {
        "tenant_key": "default",
        "channel_key": "website",
        "session_id": "session-stream-safety",
        "client_message_id": client_message_id,
        "body": body,
        "recent_context": [],
    }


def _parse_sse(body: str) -> list[tuple[str, dict]]:
    events = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        event = "message"
        data_lines = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].lstrip())
        if data_lines:
            events.append((event, json.loads("\n".join(data_lines))))
    return events


def _settings():
    return SimpleNamespace(
        stream_enabled=True,
        stream_require_accept=True,
        provider_runtime_agent_id="webchat-fast",
        is_external_channel_stream_configured=True,
    )


def _ai_reply(text: str, *, intent: str = "general_support", handoff: bool = False, tracking: str | None = None) -> WebchatFastReplyResult:
    return WebchatFastReplyResult(
        ok=True,
        ai_generated=True,
        reply_source="provider_runtime",
        reply=text,
        intent=intent,
        tracking_number=tracking,
        handoff_required=handoff,
        handoff_reason="manual_review_required" if handoff else None,
        recommended_agent_action="Review this request." if handoff else None,
        ticket_creation_queued=False,
        elapsed_ms=20,
    )


def test_only_customer_visible_surfaces_are_exposed_and_final_intent_is_allowed(monkeypatch):
    async def fake_generate(**kwargs):
        return _ai_reply("Hello", intent="general_support")

    monkeypatch.setattr(webchat_fast, "get_webchat_fast_settings", _settings)
    monkeypatch.setattr(webchat_fast, "enforce_webchat_fast_rate_limit", lambda *a, **k: None)
    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)

    response = client.post("/api/webchat/fast-reply/stream", json=_payload(), headers={"Accept": "text/event-stream"})
    events = _parse_sse(response.text)
    visible = [payload["text"] for event, payload in events if event == "reply_delta"]
    finals = [payload for event, payload in events if event == "final"]

    assert response.status_code == 200
    assert visible == ["Hello"]
    assert finals[0]["intent"] == "general_support"
    assert finals[0]["handoff_required"] is False
    assert finals[0]["ai_decision_trace"]["policy_gate"]["ok"] is True
    assert "reply" not in finals[0]


def test_tracking_claim_without_trusted_fact_returns_safe_stream_fallback(monkeypatch):
    def fake_lookup_fast_tracking_fact(**kwargs):
        return TrackingFactResult(
            ok=False,
            tracking_number=kwargs["tracking_number"],
            tool_status="timeout",
            pii_redacted=True,
            fact_evidence_present=False,
            failure_reason="timeout",
        )

    async def fake_generate(**kwargs):
        return _ai_reply("Your parcel ending 006856 is delivered.", intent="tracking", tracking="CH020000006856")

    monkeypatch.setattr(webchat_fast, "get_webchat_fast_settings", _settings)
    monkeypatch.setattr(webchat_fast, "enforce_webchat_fast_rate_limit", lambda *a, **k: None)
    monkeypatch.setattr(webchat_fast, "_lookup_fast_tracking_fact", fake_lookup_fast_tracking_fact)
    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)

    response = client.post(
        "/api/webchat/fast-reply/stream",
        json=_payload("tracking-policy-block", body="CH020000006856这是我的订单号"),
        headers={"Accept": "text/event-stream"},
    )
    events = _parse_sse(response.text)
    final = [payload for event, payload in events if event == "final"][0]

    assert response.status_code == 200
    assert final["reply_source"] == "server_safe_fallback"
    assert final["ai_generated"] is False
    assert final["handoff_required"] is True
    assert final["ai_decision_trace"]["policy_gate"]["ok"] is False
    assert "CH020000006856" not in response.text

    db = SessionLocal()
    try:
        row = db.execute(select(WebchatFastIdempotency).where(WebchatFastIdempotency.client_message_id == "tracking-policy-block")).scalar_one()
        assert row.status == "done"
        assert row.response_json["reply_source"] == "server_safe_fallback"
        assert db.execute(text("select count(*) from background_jobs")).scalar_one() == 0
    finally:
        db.close()


def test_handoff_tool_execution_failure_does_not_emit_final_success(monkeypatch):
    async def fake_generate(**kwargs):
        return _ai_reply("A human teammate will review this.", intent="handoff_request", handoff=True)

    def fail_execute(*args, **kwargs):
        raise RuntimeError("tool execution unavailable")

    monkeypatch.setattr(webchat_fast, "get_webchat_fast_settings", _settings)
    monkeypatch.setattr(webchat_fast, "enforce_webchat_fast_rate_limit", lambda *a, **k: None)
    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)
    monkeypatch.setattr(webchat_fast, "execute_decision_tools", fail_execute)

    response = client.post(
        "/api/webchat/fast-reply/stream",
        json=_payload("handoff-execution-failed"),
        headers={"Accept": "text/event-stream"},
    )
    events = _parse_sse(response.text)

    assert any(event == "error" and payload.get("error_code") == "stream_internal_error" for event, payload in events)
    assert not any(event == "final" for event, _ in events)

    db = SessionLocal()
    try:
        row = db.execute(select(WebchatFastIdempotency).where(WebchatFastIdempotency.client_message_id == "handoff-execution-failed")).scalar_one()
        assert row.status == "failed"
        assert row.error_code == "stream_internal_error"
        assert db.execute(select(BackgroundJob)).scalars().all() == []
        assert db.execute(select(Ticket)).scalars().all() == []
    finally:
        db.close()
