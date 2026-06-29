from __future__ import annotations

from sqlalchemy import func, select

from app.models import WebchatRateLimitBucket
from app.services.webchat_fast_ai_service import WebchatFastReplyResult
from app.services.webchat_fast_config import get_webchat_fast_settings
from app.services.webchat_fast_idempotency_db import WebchatFastIdempotency
from app.webchat_models import WebchatConversation, WebchatMessage
from test_webchat_fast_reply_api import SessionLocal, client, _payload, setup_function  # noqa: F401
from app.api import webchat_fast


def _support_hours_payload(client_message_id: str = "support-hours-1") -> dict:
    payload = _payload(
        client_message_id,
        body="What are your customer service hours for parcel delivery support?",
    )
    payload["session_id"] = "support-hours-session"
    return payload


def _ai_support_hours_reply() -> WebchatFastReplyResult:
    return WebchatFastReplyResult(
        ok=True,
        ai_generated=True,
        reply_source="provider_runtime",
        reply="AI parcel support is available 24/7. Human support hours can be confirmed by the support team if needed.",
        intent="general_support",
        tracking_number=None,
        handoff_required=False,
        handoff_reason=None,
        recommended_agent_action=None,
        ticket_creation_queued=False,
        elapsed_ms=20,
    )


def test_support_hours_json_uses_ai_decision_runtime_pipeline(monkeypatch):
    async def fake_generate(**kwargs):
        assert kwargs["body"] == "What are your customer service hours for parcel delivery support?"
        return _ai_support_hours_reply()

    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)
    headers = {"Origin": "http://localhost", "User-Agent": "pytest-support-hours/1.0"}
    first = client.post("/api/webchat/fast-reply", json=_support_hours_payload(), headers=headers)
    second = client.post("/api/webchat/fast-reply", json=_support_hours_payload(), headers=headers)

    assert first.status_code == 200
    assert first.headers["access-control-allow-origin"] == "http://localhost"
    data = first.json()
    assert data["reply_source"] == "provider_runtime"
    assert data["ai_generated"] is True
    assert data["handoff_required"] is False
    assert data["ai_decision_trace"]["schema_version"] == "webchat_ai_decision_v1"
    assert data["ai_decision_trace"]["policy_gate"]["ok"] is True
    assert second.status_code == 200
    assert second.json()["idempotent"] is True

    db = SessionLocal()
    try:
        assert db.execute(select(func.count(WebchatRateLimitBucket.id))).scalar_one() == 1
        idem = db.execute(select(WebchatFastIdempotency)).scalar_one()
        assert idem.status == "done"
        assert idem.response_json["reply_source"] == "provider_runtime"
        assert idem.response_json["ai_decision_trace"]["schema_version"] == "webchat_ai_decision_v1"
        conversation = db.execute(select(WebchatConversation)).scalar_one()
        messages = db.execute(
            select(WebchatMessage)
            .where(WebchatMessage.conversation_id == conversation.id)
            .order_by(WebchatMessage.id.asc())
        ).scalars().all()
        assert [message.direction for message in messages] == ["visitor", "ai"]
        assert "ai_decision_trace" in (messages[1].metadata_json or "")
    finally:
        db.close()


def test_support_hours_stream_matches_json_decision_runtime_and_persists(monkeypatch):
    async def fake_generate(**kwargs):
        return _ai_support_hours_reply()

    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)
    monkeypatch.setenv("WEBCHAT_FAST_STREAM_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_FAST_STREAM_REQUIRE_ACCEPT", "true")
    get_webchat_fast_settings.cache_clear()

    response = client.post(
        "/api/webchat/fast-reply/stream",
        json=_support_hours_payload("support-hours-stream-1"),
        headers={"Origin": "http://localhost", "Accept": "text/event-stream"},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost"
    text = response.text
    assert "provider_runtime" in text
    assert '"ai_decision_trace":' in text
    assert '"policy_gate":{"ok":true' in text
    assert "event: reply_delta" in text
    assert "event: final" in text

    db = SessionLocal()
    try:
        idem = db.execute(select(WebchatFastIdempotency)).scalar_one()
        assert idem.status == "done"
        assert idem.response_json["reply_source"] == "provider_runtime"
        assert idem.response_json["ai_decision_trace"]["schema_version"] == "webchat_ai_decision_v1"
        conversation = db.execute(select(WebchatConversation)).scalar_one()
        messages = db.execute(
            select(WebchatMessage)
            .where(WebchatMessage.conversation_id == conversation.id)
            .order_by(WebchatMessage.id.asc())
        ).scalars().all()
        assert [message.direction for message in messages] == ["visitor", "ai"]
    finally:
        db.close()
