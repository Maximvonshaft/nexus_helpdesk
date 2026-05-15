from __future__ import annotations

import os
import json
from types import SimpleNamespace

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_fast_stream_replay_safety.db")
os.environ.setdefault("WEBCHAT_FAST_AI_ENABLED", "false")

from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.api import webchat_fast
from app.db import Base, SessionLocal, engine
from app.main import app
from app.services.webchat_fast_idempotency_db import (
    WebchatFastIdempotency,
    begin_webchat_fast_idempotency,
    compute_request_hash,
    mark_webchat_fast_done,
)

pytestmark = __import__("pytest").mark.fast_lane_v2_2_2

client = TestClient(app)


def setup_function():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        db.execute(delete(WebchatFastIdempotency))
        db.commit()
    finally:
        db.close()


def _settings():
    return SimpleNamespace(
        stream_enabled=True,
        stream_require_accept=True,
        openclaw_responses_agent_id="webchat-fast",
        is_openclaw_stream_configured=True,
        stream_rollout_percent=100,
        app_env="test",
    )


def _payload(client_message_id: str) -> dict:
    return {
        "tenant_key": "default",
        "channel_key": "website",
        "session_id": "session-stream-replay-safety",
        "client_message_id": client_message_id,
        "body": "Hi",
        "recent_context": [],
    }


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


def _seed_done_replay_row(*, payload: dict, response_json: dict) -> None:
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
        begin = begin_webchat_fast_idempotency(
            db,
            tenant_key=payload["tenant_key"],
            session_id=payload["session_id"],
            client_message_id=payload["client_message_id"],
            request_hash=request_hash,
            owner_request_id="seed-replay-row",
        )
        assert begin.kind == "owner"
        assert begin.row is not None
        mark_webchat_fast_done(db, begin.row, response_json=response_json)
        db.commit()
    finally:
        db.close()


def test_stream_replay_revalidates_stored_reply_before_reply_delta(monkeypatch):
    monkeypatch.setattr(webchat_fast, "get_webchat_fast_settings", _settings)
    monkeypatch.setattr(webchat_fast, "enforce_webchat_fast_rate_limit", lambda *a, **k: None)

    payload = _payload("unsafe-stored-replay")
    _seed_done_replay_row(
        payload=payload,
        response_json={
            "ok": True,
            "ai_generated": True,
            "reply_source": "openclaw_responses_stream",
            "reply": "Your refund has been approved and processed.",
            "intent": "tracking",
            "tracking_number": None,
            "handoff_required": False,
            "handoff_reason": None,
            "ticket_creation_queued": False,
        },
    )

    response = client.post("/api/webchat/fast-reply/stream", json=payload, headers={"Accept": "text/event-stream"})
    assert response.status_code == 200
    events = _parse_sse(response.text)

    assert ("meta", {"replayed": True}) in events
    assert any(event == "error" and data.get("error_code") == "ai_invalid_output" and data.get("replayed") is True for event, data in events)
    assert not any(event == "reply_delta" for event, _ in events)
    assert not any(event == "final" for event, _ in events)


def test_stream_replay_safe_stored_reply_still_emits_delta_and_omits_reply_from_final(monkeypatch):
    monkeypatch.setattr(webchat_fast, "get_webchat_fast_settings", _settings)
    monkeypatch.setattr(webchat_fast, "enforce_webchat_fast_rate_limit", lambda *a, **k: None)

    payload = _payload("safe-stored-replay")
    _seed_done_replay_row(
        payload=payload,
        response_json={
            "ok": True,
            "ai_generated": True,
            "reply_source": "openclaw_responses_stream",
            "reply": "  Hi, this is Speedy. Please share your tracking number so I can help check it.  ",
            "intent": "tracking_missing_number",
            "tracking_number": None,
            "handoff_required": False,
            "handoff_reason": None,
            "ticket_creation_queued": False,
        },
    )

    response = client.post("/api/webchat/fast-reply/stream", json=payload, headers={"Accept": "text/event-stream"})
    assert response.status_code == 200
    events = _parse_sse(response.text)

    deltas = [data["text"] for event, data in events if event == "reply_delta"]
    finals = [data for event, data in events if event == "final"]
    assert deltas == ["Hi, this is Speedy. Please share your tracking number so I can help check it."]
    assert len(finals) == 1
    assert finals[0]["replayed"] is True
    assert finals[0]["intent"] == "tracking_missing_number"
    assert "reply" not in finals[0]
