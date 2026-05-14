from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_fast_audit_regressions.db")
os.environ.setdefault("WEBCHAT_FAST_AI_ENABLED", "false")

from fastapi.testclient import TestClient
from sqlalchemy import delete, text
from starlette.requests import Request

from app.api import webchat_fast
from app.db import Base, SessionLocal, engine
from app.main import app
from app.services import webchat_fast_rate_limit as rate_limit
from app.services.webchat_fast_ai_service import WebchatFastReplyResult
from app.services.webchat_fast_idempotency_db import (
    WebchatFastIdempotency,
    begin_webchat_fast_idempotency,
    compute_request_hash,
    mark_webchat_fast_failed,
)
from app.services.webchat_fast_rate_limit import reset_webchat_fast_rate_limit_for_tests


client = TestClient(app)


def setup_function():
    db = SessionLocal()
    try:
        db.execute(text("DROP TABLE IF EXISTS webchat_rate_limits"))
        db.commit()
    finally:
        db.close()
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        db.execute(delete(WebchatFastIdempotency))
        db.commit()
    finally:
        db.close()
    reset_webchat_fast_rate_limit_for_tests()


def _payload(client_message_id: str = "client-audit-regression") -> dict:
    return {
        "tenant_key": "default",
        "channel_key": "website",
        "session_id": "session-audit-regression",
        "client_message_id": client_message_id,
        "body": "Hi",
        "recent_context": [],
    }


def _rate_limit_settings(**overrides):
    values = {
        "trusted_proxy_cidrs": ("127.0.0.1/32", "172.16.0.0/12"),
        "rate_limit_trust_x_forwarded_for": True,
        "rate_limit_window_seconds": 60,
        "rate_limit_max_requests": 30,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _request(*, client_ip: str = "172.16.0.10", xff: str | None = None) -> Request:
    headers: list[tuple[bytes, bytes]] = [(b"user-agent", b"pytest-fast-audit-regression/1.0")]
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode("utf-8")))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/webchat/fast-reply",
        "headers": headers,
        "client": (client_ip, 12345),
        "scheme": "http",
        "query_string": b"",
        "server": ("testserver", 80),
    }
    return Request(scope)


def test_retryable_ai_failure_does_not_poison_non_stream_fallback(monkeypatch):
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

    request_payload = _payload("client-retryable-ai-invalid")
    request_hash = compute_request_hash(
        tenant_key=request_payload["tenant_key"],
        channel_key=request_payload["channel_key"],
        session_id=request_payload["session_id"],
        client_message_id=request_payload["client_message_id"],
        body=request_payload["body"],
        recent_context=request_payload["recent_context"],
    )
    db = SessionLocal()
    try:
        begin = begin_webchat_fast_idempotency(
            db,
            tenant_key=request_payload["tenant_key"],
            session_id=request_payload["session_id"],
            client_message_id=request_payload["client_message_id"],
            request_hash=request_hash,
            owner_request_id="stream-owner",
        )
        mark_webchat_fast_failed(db, begin.row, error_code="ai_invalid_output")
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)

    response = client.post("/api/webchat/fast-reply", json=request_payload)

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert calls["generate"] == 1


def test_trusted_proxy_uses_rightmost_untrusted_public_xff(monkeypatch):
    monkeypatch.setattr(rate_limit, "get_webchat_fast_settings", lambda: _rate_limit_settings())

    request = _request(client_ip="172.16.0.10", xff="8.8.8.8, 1.1.1.1, 172.16.0.10")

    assert rate_limit.trusted_client_ip(request) == "1.1.1.1"


def test_spoofed_leftmost_xff_cannot_rotate_bucket_identity(monkeypatch):
    monkeypatch.setattr(rate_limit, "get_webchat_fast_settings", lambda: _rate_limit_settings())

    first = _request(client_ip="172.16.0.10", xff="8.8.8.8, 1.1.1.1")
    second = _request(client_ip="172.16.0.10", xff="9.9.9.9, 1.1.1.1")

    assert rate_limit.trusted_client_ip(first) == "1.1.1.1"
    assert rate_limit.trusted_client_ip(second) == "1.1.1.1"


def test_untrusted_remote_ignores_xff(monkeypatch):
    monkeypatch.setattr(rate_limit, "get_webchat_fast_settings", lambda: _rate_limit_settings())

    request = _request(client_ip="8.8.4.4", xff="1.1.1.1")

    assert rate_limit.trusted_client_ip(request) == "8.8.4.4"
