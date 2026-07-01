from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.api import webchat_fast
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Customer, Ticket
from app.services.tracking_fact_schema import TrackingFactResult
from app.services.webchat_fast_ai_service import WebchatFastReplyResult
from app.services.webchat_fast_idempotency_db import WebchatFastIdempotency
from app.services.webchat_fast_stream_service import StreamBeginOutcome
from app.webchat_models import WebchatConversation, WebchatHandoffRequest, WebchatMessage

pytestmark = pytest.mark.fast_lane_v2_2_2

client = TestClient(app)


def setup_function():
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


def _payload(client_message_id: str = "client-stream-1", *, body: str = "Hi") -> dict:
    return {
        "tenant_key": "default",
        "channel_key": "website",
        "session_id": f"session-{client_message_id}",
        "client_message_id": client_message_id,
        "body": body,
        "recent_context": [],
    }


def _settings(enabled: bool = True):
    return SimpleNamespace(
        stream_enabled=enabled,
        stream_require_accept=True,
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


def _ai_reply(
    text: str,
    *,
    intent: str = "general_support",
    handoff: bool = False,
    handoff_reason: str | None = None,
    tracking: str | None = None,
) -> WebchatFastReplyResult:
    return WebchatFastReplyResult(
        ok=True,
        ai_generated=True,
        reply_source="provider_runtime",
        reply=text,
        intent=intent,
        tracking_number=tracking,
        handoff_required=handoff,
        handoff_reason=handoff_reason or ("customer_requested_human_review" if handoff else None),
        recommended_agent_action="Review the conversation and reply with verified information." if handoff else None,
        ticket_creation_queued=False,
        elapsed_ms=20,
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


def test_successful_stream_contract_uses_ai_decision_runtime(monkeypatch):
    async def fake_generate(**kwargs):
        assert kwargs["body"] == "Hi"
        return _ai_reply("Hello. How can I help you today?", intent="general_support")

    monkeypatch.setattr(webchat_fast, "get_webchat_fast_settings", lambda: _settings(True))
    monkeypatch.setattr(webchat_fast, "enforce_webchat_fast_rate_limit", lambda *a, **k: None)
    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)

    response = client.post(
        "/api/webchat/fast-reply/stream",
        json=_payload("client-stream-success", body="Hi"),
        headers={"Accept": "text/event-stream"},
    )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    events = _parse_sse(response.text)
    assert [event for event, _payload in events] == ["meta", "final", "reply_delta"]
    final = [payload for event, payload in events if event == "final"][0]
    assert final["ok"] is True
    assert final["ai_generated"] is True
    assert final["reply_source"] == "provider_runtime"
    assert final["handoff_required"] is False
    assert final["ai_decision_trace"]["schema_version"] == "webchat_ai_decision_v1"
    assert final["ai_decision_trace"]["policy_gate"]["ok"] is True
    assert "reply" not in final
    assert [payload for event, payload in events if event == "reply_delta"][0]["text"] == "Hello. How can I help you today?"


def test_stream_provider_failure_returns_safe_final_without_500(monkeypatch):
    async def fake_generate(**kwargs):
        return WebchatFastReplyResult(
            ok=False,
            ai_generated=False,
            reply_source=None,
            reply=None,
            intent=None,
            tracking_number=None,
            handoff_required=False,
            handoff_reason=None,
            recommended_agent_action=None,
            ticket_creation_queued=False,
            elapsed_ms=30,
            error_code="ai_unavailable",
        )

    monkeypatch.setattr(webchat_fast, "get_webchat_fast_settings", lambda: _settings(True))
    monkeypatch.setattr(webchat_fast, "enforce_webchat_fast_rate_limit", lambda *a, **k: None)
    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)

    response = client.post(
        "/api/webchat/fast-reply/stream",
        json=_payload("client-stream-fallback", body="What services do you offer?"),
        headers={"Accept": "text/event-stream"},
    )

    assert response.status_code == 200
    events = _parse_sse(response.text)
    final = [payload for event, payload in events if event == "final"][0]
    assert final["reply_source"] == "server_safe_fallback"
    assert final["ai_generated"] is False
    assert final["handoff_required"] is True
    assert final["ai_decision_trace"]["mode"] in {"emergency_fallback_only", "gated"}
    assert "ExternalChannel" not in response.text


def test_stream_no_evidence_low_signal_still_calls_ai_decision(monkeypatch):
    calls = {"ai": 0}

    async def fake_generate(**kwargs):
        calls["ai"] += 1
        return _ai_reply("Please tell me what you need help with.", intent="unclear")

    monkeypatch.setattr(webchat_fast, "get_webchat_fast_settings", lambda: _settings(True))
    monkeypatch.setattr(webchat_fast, "enforce_webchat_fast_rate_limit", lambda *a, **k: None)
    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)

    response = client.post(
        "/api/webchat/fast-reply/stream",
        json=_payload("client-stream-low-signal", body="321"),
        headers={"Accept": "text/event-stream"},
    )

    assert response.status_code == 200
    text = response.text
    assert '"reply_source":"provider_runtime"' in text
    assert '"ai_generated":true' in text
    assert '"handoff_required":false' in text
    assert '"ai_decision_trace":' in text
    assert '"server_knowledge_no_evidence"' not in text
    assert calls == {"ai": 1}


def test_stream_tracking_status_claim_without_fact_is_policy_blocked(monkeypatch):
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

    monkeypatch.setattr(webchat_fast, "get_webchat_fast_settings", lambda: _settings(True))
    monkeypatch.setattr(webchat_fast, "enforce_webchat_fast_rate_limit", lambda *a, **k: None)
    monkeypatch.setattr(webchat_fast, "_lookup_fast_tracking_fact", fake_lookup_fast_tracking_fact)
    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)

    response = client.post(
        "/api/webchat/fast-reply/stream",
        json=_payload("client-stream-tracking-blocked", body="CH020000006856这是我的订单号"),
        headers={"Accept": "text/event-stream"},
    )

    assert response.status_code == 200
    text = response.text
    assert '"reply_source":"server_safe_fallback"' in text
    assert '"handoff_required":true' in text
    assert '"policy_gate":{"ok":false' in text
    assert '"raw_tracking_number_exposed":false' in text
    assert "CH020000006856" not in text


def test_active_processing_returns_202_before_streaming(monkeypatch):
    calls = {"ai": 0}

    async def fake_generate(**kwargs):
        calls["ai"] += 1
        return _ai_reply("should not run")

    monkeypatch.setattr(webchat_fast, "get_webchat_fast_settings", lambda: _settings(True))
    monkeypatch.setattr(webchat_fast, "enforce_webchat_fast_rate_limit", lambda *a, **k: None)
    monkeypatch.setattr(
        webchat_fast,
        "prepare_webchat_fast_stream",
        lambda **kwargs: StreamBeginOutcome(status="processing", request_hash="h"),
    )
    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)

    response = client.post(
        "/api/webchat/fast-reply/stream",
        json=_payload("client-stream-processing"),
        headers={"Accept": "text/event-stream"},
    )
    assert response.status_code == 202
    assert response.json()["error_code"] == "request_processing"
    assert calls == {"ai": 0}


def test_stream_idempotent_replay_not_polluted_by_fallback(monkeypatch):
    calls = {"ai": 0}

    async def fake_generate(**kwargs):
        calls["ai"] += 1
        return _ai_reply("Hello from one stream run.", intent="general_support")

    monkeypatch.setattr(webchat_fast, "get_webchat_fast_settings", lambda: _settings(True))
    monkeypatch.setattr(webchat_fast, "enforce_webchat_fast_rate_limit", lambda *a, **k: None)
    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)

    payload = _payload("client-stream-idempotent", body="Hi")
    first = client.post("/api/webchat/fast-reply/stream", json=payload, headers={"Accept": "text/event-stream"})
    second = client.post("/api/webchat/fast-reply/stream", json=payload, headers={"Accept": "text/event-stream"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert '"replayed":true' in second.text
    assert calls == {"ai": 1}
    db = SessionLocal()
    try:
        row = db.execute(select(WebchatFastIdempotency).where(WebchatFastIdempotency.client_message_id == "client-stream-idempotent")).scalar_one()
        assert row.status == "done"
        assert row.response_json["reply_source"] == "provider_runtime"
        assert row.response_json["ai_decision_trace"]["policy_gate"]["ok"] is True
    finally:
        db.close()
