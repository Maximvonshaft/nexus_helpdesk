from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_fast_reply_api_tests.db")
os.environ.setdefault("WEBCHAT_FAST_AI_ENABLED", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app.api import webchat_fast  # noqa: E402
from app.main import app  # noqa: E402
from app.services.webchat_fast_ai_service import WebchatFastReplyResult  # noqa: E402
from app.services.webchat_fast_idempotency import reset_fast_reply_idempotency_for_tests  # noqa: E402
from app.services.webchat_fast_rate_limit import reset_webchat_fast_rate_limit_for_tests  # noqa: E402


client = TestClient(app)


def setup_function():
    reset_fast_reply_idempotency_for_tests()
    reset_webchat_fast_rate_limit_for_tests()


def _payload(client_message_id: str = "client-1") -> dict:
    return {
        "tenant_key": "default",
        "channel_key": "website",
        "session_id": "session-1",
        "client_message_id": client_message_id,
        "body": "Hi",
        "recent_context": [],
    }


def test_fast_reply_normal_path_does_not_open_db(monkeypatch):
    async def fake_generate(**kwargs):
        return WebchatFastReplyResult(
            ok=True,
            ai_generated=True,
            reply_source="openclaw_responses",
            reply="Hi, this is Speedy. How can I help you today?",
            intent="greeting",
            tracking_number=None,
            handoff_required=False,
            handoff_reason=None,
            recommended_agent_action=None,
            ticket_creation_queued=False,
            elapsed_ms=25,
        )

    def fail_db_context():
        raise AssertionError("normal fast reply path must not open db_context")

    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)
    monkeypatch.setattr(webchat_fast, "db_context", fail_db_context)

    response = client.post("/api/webchat/fast-reply", json=_payload())

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["ai_generated"] is True
    assert data["reply_source"] == "openclaw_responses"
    assert data["handoff_required"] is False
    assert data["ticket_creation_queued"] is False


def test_fast_reply_handoff_enqueues_job_but_returns_ai_reply(monkeypatch):
    calls = {"db_opened": 0, "enqueued": 0}

    async def fake_generate(**kwargs):
        return WebchatFastReplyResult(
            ok=True,
            ai_generated=True,
            reply_source="openclaw_responses",
            reply="I’ll route this to a support specialist for checking.",
            intent="handoff",
            tracking_number="SF123456789",
            handoff_required=True,
            handoff_reason="manual_review_required",
            recommended_agent_action="Check shipment status and reply with verified information.",
            ticket_creation_queued=False,
            elapsed_ms=30,
        )

    class FakeContext:
        def __enter__(self):
            calls["db_opened"] += 1
            return object()

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_db_context():
        return FakeContext()

    def fake_enqueue(db, *, snapshot):
        calls["enqueued"] += 1
        assert snapshot["tracking_number"] == "SF123456789"
        assert snapshot["customer_last_message"] == "Hi"
        return object()

    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)
    monkeypatch.setattr(webchat_fast, "db_context", fake_db_context)
    monkeypatch.setattr(webchat_fast, "enqueue_webchat_handoff_snapshot_job", fake_enqueue)

    response = client.post("/api/webchat/fast-reply", json=_payload("client-2"))

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["ai_generated"] is True
    assert data["handoff_required"] is True
    assert data["ticket_creation_queued"] is True
    assert calls == {"db_opened": 1, "enqueued": 1}


def test_handoff_enqueue_failure_does_not_block_ai_reply(monkeypatch):
    async def fake_generate(**kwargs):
        return WebchatFastReplyResult(
            ok=True,
            ai_generated=True,
            reply_source="openclaw_responses",
            reply="I’ll route this to a support specialist for checking.",
            intent="handoff",
            tracking_number=None,
            handoff_required=True,
            handoff_reason="manual_review_required",
            recommended_agent_action="Review the request.",
            ticket_creation_queued=False,
            elapsed_ms=30,
        )

    class FailingContext:
        def __enter__(self):
            raise RuntimeError("db unavailable")

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)
    monkeypatch.setattr(webchat_fast, "db_context", lambda: FailingContext())

    response = client.post("/api/webchat/fast-reply", json=_payload("client-3"))

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["ai_generated"] is True
    assert data["handoff_required"] is True
    assert data["ticket_creation_queued"] is False


def test_ai_unavailable_returns_no_reply(monkeypatch):
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
            elapsed_ms=10,
            error_code="ai_unavailable",
            retry_after_ms=1500,
        )

    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)

    response = client.post("/api/webchat/fast-reply", json=_payload("client-4"))

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["ai_generated"] is False
    assert data["reply"] is None
    assert data["error_code"] == "ai_unavailable"


def test_idempotent_fast_reply_returns_same_response(monkeypatch):
    calls = {"generate": 0}

    async def fake_generate(**kwargs):
        calls["generate"] += 1
        return WebchatFastReplyResult(
            ok=True,
            ai_generated=True,
            reply_source="openclaw_responses",
            reply="Hi, this is Speedy.",
            intent="greeting",
            tracking_number=None,
            handoff_required=False,
            handoff_reason=None,
            recommended_agent_action=None,
            ticket_creation_queued=False,
            elapsed_ms=20,
        )

    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)

    first = client.post("/api/webchat/fast-reply", json=_payload("client-idempotent"))
    second = client.post("/api/webchat/fast-reply", json=_payload("client-idempotent"))

    assert first.status_code == 200
    assert second.status_code == 200
    assert calls["generate"] == 1
    assert second.json()["idempotent"] is True
