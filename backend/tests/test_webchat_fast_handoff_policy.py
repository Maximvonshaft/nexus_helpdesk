from __future__ import annotations

import json
import os
from types import SimpleNamespace

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_fast_handoff_policy.db")
os.environ.setdefault("WEBCHAT_FAST_AI_ENABLED", "false")

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.api import webchat_fast
from app.db import Base, SessionLocal, engine
from app.main import app
from app.services.webchat_fast_idempotency_db import WebchatFastIdempotency
from app.services.webchat_handoff_policy import decide_server_handoff_policy


client = TestClient(app)


def setup_function():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        db.execute(delete(WebchatFastIdempotency))
        db.commit()
    finally:
        db.close()


def _payload(client_message_id: str, body: str) -> dict:
    return {
        "tenant_key": "default",
        "channel_key": "website",
        "session_id": "session-handoff-policy",
        "client_message_id": client_message_id,
        "body": body,
        "recent_context": [],
    }


def _stream_settings():
    return SimpleNamespace(
        stream_enabled=True,
        stream_require_accept=True,
        is_openclaw_stream_configured=True,
        stream_rollout_percent=100,
        openclaw_responses_agent_id="support",
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


def test_server_policy_detects_business_mandatory_handoff_terms():
    cases = [
        ("I want a human to review this", "explicit_human_request"),
        ("I want compensation for my lost parcel", "refund_compensation_claim"),
        ("请帮我改地址", "address_change_request"),
        ("My parcel is stuck at customs", "customs_clearance_issue"),
        ("包裹破损了，我要投诉", "complaint_or_escalation"),
    ]
    for body, expected_rule in cases:
        decision = decide_server_handoff_policy(body=body, recent_context=[])
        assert decision.handoff_required is True
        assert decision.rule_id == expected_rule
        assert decision.customer_reply
        assert decision.recommended_agent_action and expected_rule in decision.recommended_agent_action


def test_non_stream_server_policy_handoff_skips_ai_and_enqueues(monkeypatch):
    calls = {"ai": 0, "enqueue": 0}

    async def fail_if_ai_called(**kwargs):
        calls["ai"] += 1
        raise AssertionError("server policy handoff must not call AI")

    def fake_enqueue(db, *, snapshot):
        calls["enqueue"] += 1
        assert snapshot["intent"] == "handoff"
        assert snapshot["handoff_reason"] == "address_change_requires_human_review"
        assert snapshot["recommended_agent_action"].startswith("[address_change_request]")
        assert snapshot["customer_last_message"] == "Please change address for this parcel"
        return object()

    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fail_if_ai_called)
    monkeypatch.setattr(webchat_fast, "enqueue_webchat_handoff_snapshot_job", fake_enqueue)

    response = client.post(
        "/api/webchat/fast-reply",
        json=_payload("server-policy-non-stream", "Please change address for this parcel"),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["ai_generated"] is False
    assert data["reply_source"] == "server_handoff_policy"
    assert data["handoff_required"] is True
    assert data["handoff_reason"] == "address_change_requires_human_review"
    assert data["ticket_creation_queued"] is True
    assert calls == {"ai": 0, "enqueue": 1}

    db = SessionLocal()
    try:
        row = db.execute(
            select(WebchatFastIdempotency).where(WebchatFastIdempotency.client_message_id == "server-policy-non-stream")
        ).scalar_one()
        assert row.status == "done"
        assert row.response_json["reply_source"] == "server_handoff_policy"
    finally:
        db.close()


def test_stream_server_policy_handoff_skips_openclaw_and_enqueues(monkeypatch):
    calls = {"stream": 0, "enqueue": 0}

    async def fail_if_stream_called(**kwargs):
        calls["stream"] += 1
        raise AssertionError("server policy handoff must not call OpenClaw stream")

    def fake_enqueue(db, *, snapshot):
        calls["enqueue"] += 1
        assert snapshot["intent"] == "handoff"
        assert snapshot["handoff_reason"] == "refund_or_compensation_requires_human_review"
        assert snapshot["recommended_agent_action"].startswith("[refund_compensation_claim]")
        return object()

    monkeypatch.setattr(webchat_fast, "get_webchat_fast_settings", _stream_settings)
    monkeypatch.setattr(webchat_fast, "stream_webchat_fast_reply_events", fail_if_stream_called)
    monkeypatch.setattr(webchat_fast, "enqueue_webchat_handoff_snapshot_job", fake_enqueue)

    response = client.post(
        "/api/webchat/fast-reply/stream",
        json=_payload("server-policy-stream", "I want compensation for this lost parcel"),
        headers={"Accept": "text/event-stream"},
    )
    events = _parse_sse(response.text)

    assert response.status_code == 200
    assert any(event == "reply_delta" for event, _ in events)
    finals = [payload for event, payload in events if event == "final"]
    assert len(finals) == 1
    assert finals[0]["ok"] is True
    assert finals[0]["ai_generated"] is False
    assert finals[0]["reply_source"] == "server_handoff_policy"
    assert finals[0]["handoff_required"] is True
    assert finals[0]["handoff_reason"] == "refund_or_compensation_requires_human_review"
    assert "reply" not in finals[0]
    assert calls == {"stream": 0, "enqueue": 1}

    db = SessionLocal()
    try:
        row = db.execute(
            select(WebchatFastIdempotency).where(WebchatFastIdempotency.client_message_id == "server-policy-stream")
        ).scalar_one()
        assert row.status == "done"
        assert row.response_json["reply_source"] == "server_handoff_policy"
    finally:
        db.close()
