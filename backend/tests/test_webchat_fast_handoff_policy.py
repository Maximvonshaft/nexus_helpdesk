from __future__ import annotations

import json
import os
from types import SimpleNamespace

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_fast_handoff_policy.db")
os.environ.setdefault("WEBCHAT_FAST_AI_ENABLED", "true")

from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.api import webchat_fast
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Customer, Ticket
from app.services.webchat_fast_ai_service import WebchatFastReplyResult
from app.services.webchat_fast_idempotency_db import WebchatFastIdempotency
from app.services.webchat_fast_rate_limit import reset_webchat_fast_rate_limit_for_tests
from app.services.webchat_handoff_policy import decide_server_handoff_policy
from app.webchat_models import WebchatConversation, WebchatHandoffRequest, WebchatMessage

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
    reset_webchat_fast_rate_limit_for_tests()


def _payload(client_message_id: str, body: str, session_id: str | None = None) -> dict:
    return {
        "tenant_key": "default",
        "channel_key": "website",
        "session_id": session_id or f"session-handoff-policy-{client_message_id}",
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
        openclaw_responses_agent_id="webchat-fast",
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


def _ai_handoff_reply(*, intent: str, reason: str) -> WebchatFastReplyResult:
    return WebchatFastReplyResult(
        ok=True,
        ai_generated=True,
        reply_source="openclaw_responses",
        reply="I’ll ask a human teammate to review this request before any controlled action is taken.",
        intent=intent,
        tracking_number=None,
        handoff_required=True,
        handoff_reason=reason,
        recommended_agent_action="Review the request and respond with verified information.",
        ticket_creation_queued=False,
        elapsed_ms=20,
    )


def test_server_policy_remains_advisory_safety_classifier_only():
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


def test_non_stream_handoff_terms_call_ai_and_create_tool_gated_ticket(monkeypatch):
    calls = {"ai": 0}

    async def fake_ai(**kwargs):
        calls["ai"] += 1
        assert kwargs["body"] == "Please change address for this parcel"
        return _ai_handoff_reply(intent="address_change", reason="address_change_requires_human_review")

    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_ai)

    response = client.post(
        "/api/webchat/fast-reply",
        json=_payload("ai-policy-non-stream", "Please change address for this parcel"),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["ai_generated"] is True
    assert data["reply_source"] == "openclaw_responses"
    assert data["handoff_required"] is True
    assert data["handoff_reason"] == "address_change_requires_human_review"
    assert data["ticket_creation_queued"] is False
    assert data["ticket_id"]
    assert data["handoff_request_id"]
    assert data["ai_decision_trace"]["decision"]["tool_calls"][0]["tool_name"] == "handoff.request.create"
    assert data["ai_decision_trace"]["tool_execution"]["records"][0]["status"] == "executed"
    assert calls == {"ai": 1}

    db = SessionLocal()
    try:
        row = db.execute(select(WebchatFastIdempotency).where(WebchatFastIdempotency.client_message_id == "ai-policy-non-stream")).scalar_one()
        assert row.status == "done"
        assert row.response_json["reply_source"] == "openclaw_responses"
        assert row.response_json["ai_decision_trace"]["policy_gate"]["ok"] is True
        assert db.execute(select(func.count(Ticket.id))).scalar_one() == 1
        assert db.execute(select(func.count(WebchatHandoffRequest.id))).scalar_one() == 1
    finally:
        db.close()


def test_stream_handoff_terms_use_same_ai_decision_contract(monkeypatch):
    calls = {"ai": 0}

    async def fake_ai(**kwargs):
        calls["ai"] += 1
        assert kwargs["body"] == "I want compensation for this lost parcel"
        return _ai_handoff_reply(intent="complaint", reason="refund_or_compensation_requires_human_review")

    monkeypatch.setattr(webchat_fast, "get_webchat_fast_settings", _stream_settings)
    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_ai)

    response = client.post(
        "/api/webchat/fast-reply/stream",
        json=_payload("ai-policy-stream", "I want compensation for this lost parcel"),
        headers={"Accept": "text/event-stream"},
    )
    events = _parse_sse(response.text)

    assert response.status_code == 200
    assert any(event == "reply_delta" for event, _ in events)
    finals = [payload for event, payload in events if event == "final"]
    assert len(finals) == 1
    final = finals[0]
    assert final["ok"] is True
    assert final["ai_generated"] is True
    assert final["reply_source"] == "openclaw_responses"
    assert final["handoff_required"] is True
    assert final["handoff_reason"] == "refund_or_compensation_requires_human_review"
    assert final["ticket_creation_queued"] is False
    assert final["ticket_id"]
    assert final["ai_decision_trace"]["schema_version"] == "webchat_ai_decision_v1"
    assert final["ai_decision_trace"]["decision"]["tool_calls"][0]["tool_name"] == "handoff.request.create"
    assert "reply" not in final
    assert calls == {"ai": 1}

    db = SessionLocal()
    try:
        row = db.execute(select(WebchatFastIdempotency).where(WebchatFastIdempotency.client_message_id == "ai-policy-stream")).scalar_one()
        assert row.status == "done"
        assert row.response_json["reply_source"] == "openclaw_responses"
    finally:
        db.close()


def test_address_change_with_waybill_no_longer_skips_ai(monkeypatch):
    calls = {"ai": 0, "tracking": 0}
    body = "I need to change the delivery address for CH020000008030."

    def fake_tracking(**kwargs):
        calls["tracking"] += 1
        return None

    async def fake_ai(**kwargs):
        calls["ai"] += 1
        assert kwargs["body"] == body
        return _ai_handoff_reply(intent="address_change", reason="address_change_requires_human_review")

    monkeypatch.setattr(webchat_fast, "_lookup_fast_tracking_fact", fake_tracking)
    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_ai)

    response = client.post(
        "/api/webchat/fast-reply",
        json=_payload("ai-address-with-waybill", body),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["ai_generated"] is True
    assert data["reply_source"] == "openclaw_responses"
    assert data["handoff_required"] is True
    assert data["handoff_reason"] == "address_change_requires_human_review"
    assert data["tracking_number"] is None
    assert data["tracking_number_suffix"] == "008030"
    assert data["ai_decision_trace"]["decision"]["tool_calls"][0]["tool_name"] in {"speedaf.order.query", "handoff.request.create"}
    assert calls["ai"] == 1
    assert calls["tracking"] == 1


def test_refund_compensation_policy_priority_still_available_for_emergency_classification():
    decision = decide_server_handoff_policy(
        body="I want compensation for this lost parcel",
        recent_context=[{"role": "user", "content": "I need to change the delivery address for CH020000008030."}],
    )

    assert decision.handoff_required is True
    assert decision.rule_id == "refund_compensation_claim"
    assert decision.handoff_reason == "refund_or_compensation_requires_human_review"
