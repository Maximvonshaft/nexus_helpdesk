from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_fast_reply_api_tests.db")
os.environ.setdefault("WEBCHAT_FAST_AI_ENABLED", "false")

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text
from starlette.requests import Request

from app.api import webchat_fast
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import WebchatRateLimitBucket
from app.services.webchat_fast_idempotency_db import WebchatFastIdempotency, begin_webchat_fast_idempotency, compute_request_hash
from app.services.webchat_fast_ai_service import WebchatFastReplyResult
from app.services.webchat_fast_rate_limit import enforce_webchat_fast_rate_limit, reset_webchat_fast_rate_limit_for_tests


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




def _request(*, client_ip: str = "198.51.100.10", user_agent: str = "pytest-fast-limit/1.0", origin: str | None = None, referer: str | None = None, fingerprint: str | None = None) -> Request:
    headers: list[tuple[bytes, bytes]] = [(b"user-agent", user_agent.encode("utf-8"))]
    if origin is not None:
        headers.append((b"origin", origin.encode("utf-8")))
    if referer is not None:
        headers.append((b"referer", referer.encode("utf-8")))
    if fingerprint is not None:
        headers.append((b"x-webchat-client-fingerprint", fingerprint.encode("utf-8")))
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

def _payload(client_message_id: str = "client-1") -> dict:
    return {
        "tenant_key": "default",
        "channel_key": "website",
        "session_id": "session-1",
        "client_message_id": client_message_id,
        "body": "Hi",
        "recent_context": [],
    }


def test_fast_reply_normal_path_marks_db_idempotency_done(monkeypatch):
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

    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)

    response = client.post("/api/webchat/fast-reply", json=_payload())

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["ai_generated"] is True
    assert data["reply_source"] == "openclaw_responses"
    assert data["handoff_required"] is False
    assert data["ticket_creation_queued"] is False

    db = SessionLocal()
    try:
        row = db.execute(select(WebchatFastIdempotency)).scalar_one()
        assert row.status == "done"
        assert row.response_json["reply"] == "Hi, this is Speedy. How can I help you today?"
    finally:
        db.close()


def test_fast_reply_handoff_enqueues_job_but_returns_ai_reply(monkeypatch):
    calls = {"enqueued": 0}

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

    def fake_enqueue(db, *, snapshot):
        calls["enqueued"] += 1
        assert snapshot["tracking_number"] == "SF123456789"
        assert snapshot["customer_last_message"] == "Hi"
        return object()

    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)
    monkeypatch.setattr(webchat_fast, "enqueue_webchat_handoff_snapshot_job", fake_enqueue)

    response = client.post("/api/webchat/fast-reply", json=_payload("client-2"))

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["ai_generated"] is True
    assert data["handoff_required"] is True
    assert data["ticket_creation_queued"] is True
    assert calls == {"enqueued": 1}


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

    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)
    monkeypatch.setattr(webchat_fast, "enqueue_webchat_handoff_snapshot_job", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db unavailable")))

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


def test_non_stream_same_key_different_hash_returns_409(monkeypatch):
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

    first = client.post("/api/webchat/fast-reply", json=_payload("client-conflict"))
    second = client.post("/api/webchat/fast-reply", json={**_payload("client-conflict"), "body": "Different body"})

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["error_code"] == "idempotency_key_reused_with_different_payload"
    assert calls["generate"] == 1


def test_non_stream_active_processing_returns_202_without_duplicate_generation(monkeypatch):
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

    request_payload = _payload("client-processing")
    db = SessionLocal()
    try:
        begin = begin_webchat_fast_idempotency(
            db,
            tenant_key=request_payload["tenant_key"],
            session_id=request_payload["session_id"],
            client_message_id=request_payload["client_message_id"],
            request_hash=compute_request_hash(
                tenant_key=request_payload["tenant_key"],
                channel_key=request_payload["channel_key"],
                session_id=request_payload["session_id"],
                client_message_id=request_payload["client_message_id"],
                body=request_payload["body"],
                recent_context=request_payload["recent_context"],
            ),
            owner_request_id="existing-owner",
        )
        assert begin.kind == "owner"
    finally:
        db.close()

    response = client.post("/api/webchat/fast-reply", json=request_payload)

    assert response.status_code == 202
    assert response.json()["error_code"] == "request_processing"
    assert calls["generate"] == 0


def test_fast_rate_limit_is_shared_across_rotated_session_ids(monkeypatch):
    reset_webchat_fast_rate_limit_for_tests()

    async def fake_generate(**kwargs):
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
    monkeypatch.setenv("WEBCHAT_FAST_RATE_LIMIT_MAX_REQUESTS", "2")
    monkeypatch.setenv("WEBCHAT_FAST_RATE_LIMIT_WINDOW_SECONDS", "60")

    headers = {"User-Agent": "pytest-fast-limit/1.0"}
    first = client.post("/api/webchat/fast-reply", json=_payload("rl-1"), headers=headers)
    second = client.post("/api/webchat/fast-reply", json={**_payload("rl-2"), "session_id": "session-2"}, headers=headers)
    third = client.post("/api/webchat/fast-reply", json={**_payload("rl-3"), "session_id": "session-3"}, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429


def test_fast_rate_limit_concurrent_same_bucket_does_not_exceed_limit(monkeypatch):
    reset_webchat_fast_rate_limit_for_tests()
    monkeypatch.setenv("WEBCHAT_FAST_RATE_LIMIT_MAX_REQUESTS", "3")
    monkeypatch.setenv("WEBCHAT_FAST_RATE_LIMIT_WINDOW_SECONDS", "60")
    reset_webchat_fast_rate_limit_for_tests()

    barrier = Barrier(8)

    def attempt(_: int) -> bool:
        barrier.wait()
        try:
            enforce_webchat_fast_rate_limit(_request(fingerprint="fp-shared"), tenant_key="tenant-a", session_id=f"session-{_}")
            return True
        except HTTPException as exc:
            assert exc.status_code == 429
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))

    assert sum(results) == 3


def test_long_origin_and_user_agent_do_not_exceed_bucket_key_column(monkeypatch):
    reset_webchat_fast_rate_limit_for_tests()
    monkeypatch.setenv("WEBCHAT_FAST_RATE_LIMIT_MAX_REQUESTS", "3")
    monkeypatch.setenv("WEBCHAT_FAST_RATE_LIMIT_WINDOW_SECONDS", "60")
    reset_webchat_fast_rate_limit_for_tests()

    long_origin = "https://" + ("very-long-origin-segment-" * 20) + ".example.com/" + ("path/" * 80)
    long_referer = long_origin + "?" + ("ref=" + "r" * 512)
    long_user_agent = "pytest-agent/" + ("ua-" * 200)
    long_fingerprint = "fp-" + ("abcdef" * 120)

    enforce_webchat_fast_rate_limit(
        _request(origin=long_origin, referer=long_referer, user_agent=long_user_agent, fingerprint=long_fingerprint),
        tenant_key="tenant-a",
        session_id="session-1",
    )
    enforce_webchat_fast_rate_limit(
        _request(origin=long_origin, referer=long_referer, user_agent=long_user_agent, fingerprint=long_fingerprint),
        tenant_key="tenant-a",
        session_id="session-rotated",
    )
    enforce_webchat_fast_rate_limit(
        _request(origin=None, referer=long_referer, user_agent=long_user_agent, fingerprint=None),
        tenant_key="tenant-a",
        session_id="session-ua-fallback",
    )

    db = SessionLocal()
    try:
        rows = db.execute(select(WebchatRateLimitBucket).order_by(WebchatRateLimitBucket.request_count.desc())).scalars().all()
        assert len(rows) == 2
        assert all(len(row.bucket_key) == 64 for row in rows)
        assert rows[0].request_count == 2
        assert rows[1].request_count == 1
    finally:
        db.close()


def test_fast_rate_limit_different_dimensions_do_not_pollute_each_other(monkeypatch):
    reset_webchat_fast_rate_limit_for_tests()
    monkeypatch.setenv("WEBCHAT_FAST_RATE_LIMIT_MAX_REQUESTS", "1")
    monkeypatch.setenv("WEBCHAT_FAST_RATE_LIMIT_WINDOW_SECONDS", "60")
    reset_webchat_fast_rate_limit_for_tests()

    enforce_webchat_fast_rate_limit(_request(client_ip="198.51.100.10", fingerprint="fp-a"), tenant_key="tenant-a", session_id="session-1")
    enforce_webchat_fast_rate_limit(_request(client_ip="198.51.100.11", fingerprint="fp-a"), tenant_key="tenant-a", session_id="session-2")
    enforce_webchat_fast_rate_limit(_request(client_ip="198.51.100.10", fingerprint="fp-b"), tenant_key="tenant-a", session_id="session-3")
    enforce_webchat_fast_rate_limit(_request(client_ip="198.51.100.10", fingerprint="fp-a"), tenant_key="tenant-b", session_id="session-4")

    db = SessionLocal()
    try:
        rows = db.execute(select(WebchatRateLimitBucket).order_by(WebchatRateLimitBucket.bucket_key)).scalars().all()
        assert len(rows) == 4
        assert all(row.request_count == 1 for row in rows)
    finally:
        db.close()
