from __future__ import annotations

import os

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_fast_reply_api_tests.db")
os.environ.setdefault("WEBCHAT_FAST_AI_ENABLED", "false")

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.api import webchat_fast
from app.db import Base, SessionLocal, engine
from app.enums import ConversationState, EventType, JobStatus, SourceChannel, TicketStatus
from app.main import app
from app.models import BackgroundJob, Ticket, TicketEvent
from app.services.webchat_fast_idempotency_db import WebchatFastIdempotency, begin_webchat_fast_idempotency, compute_request_hash
from app.services.webchat_fast_ai_service import WebchatFastReplyResult
from app.services.webchat_fast_rate_limit import reset_webchat_fast_rate_limit_for_tests
from app.services.webchat_handoff_snapshot_service import WEBCHAT_HANDOFF_SNAPSHOT_JOB
from app.services.webchat_handoff_snapshot_worker import dispatch_pending_webchat_handoff_snapshot_jobs


client = TestClient(app)


def setup_function():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        db.execute(delete(TicketEvent))
        db.execute(delete(Ticket))
        db.execute(delete(BackgroundJob))
        db.execute(delete(WebchatFastIdempotency))
        db.commit()
    finally:
        db.close()
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


def _count_rows(model) -> int:
    db = SessionLocal()
    try:
        return len(db.execute(select(model)).scalars().all())
    finally:
        db.close()


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
    assert "recommended_agent_action" not in data

    db = SessionLocal()
    try:
        row = db.execute(select(WebchatFastIdempotency)).scalar_one()
        assert row.status == "done"
        assert row.response_json["reply"] == "Hi, this is Speedy. How can I help you today?"
        assert db.execute(select(Ticket)).scalars().all() == []
        assert db.execute(select(TicketEvent)).scalars().all() == []
        assert db.execute(select(BackgroundJob).where(BackgroundJob.job_type == WEBCHAT_HANDOFF_SNAPSHOT_JOB)).scalars().all() == []
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
    assert "recommended_agent_action" not in data
    assert calls == {"enqueued": 1}


def test_fast_reply_handoff_snapshot_worker_creates_exactly_one_ticket(monkeypatch):
    async def fake_generate(**kwargs):
        return WebchatFastReplyResult(
            ok=True,
            ai_generated=True,
            reply_source="openclaw_responses",
            reply="I’ll route this to a support specialist for checking. Please keep tracking number SPX123456789 as the reference.",
            intent="handoff",
            tracking_number="SPX123456789",
            handoff_required=True,
            handoff_reason="tracking_unresolved",
            recommended_agent_action="Check shipment SPX123456789 and reply with verified ETA if no update is available.",
            ticket_creation_queued=False,
            elapsed_ms=30,
        )

    monkeypatch.setattr(webchat_fast, "generate_webchat_fast_reply", fake_generate)

    first = client.post("/api/webchat/fast-reply", json=_payload("client-handoff-e2e"))
    second = client.post("/api/webchat/fast-reply", json=_payload("client-handoff-e2e"))

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["idempotent"] is True
    assert first.json()["handoff_required"] is True
    assert first.json()["ticket_creation_queued"] is True
    assert "recommended_agent_action" not in first.json()

    db = SessionLocal()
    try:
        jobs = db.execute(select(BackgroundJob).where(BackgroundJob.job_type == WEBCHAT_HANDOFF_SNAPSHOT_JOB)).scalars().all()
        assert len(jobs) == 1
        assert jobs[0].status == JobStatus.pending

        processed = dispatch_pending_webchat_handoff_snapshot_jobs(db, worker_id="worker-handoff-snapshot-test")
        assert len(processed) == 1

        jobs_after = db.execute(select(BackgroundJob).where(BackgroundJob.job_type == WEBCHAT_HANDOFF_SNAPSHOT_JOB)).scalars().all()
        assert len(jobs_after) == 1
        assert jobs_after[0].status == JobStatus.done

        tickets = db.execute(select(Ticket)).scalars().all()
        assert len(tickets) == 1
        ticket = tickets[0]
        assert ticket.source_channel == SourceChannel.web_chat
        assert ticket.status == TicketStatus.pending_assignment
        assert ticket.conversation_state == ConversationState.human_review_required
        assert ticket.tracking_number == "SPX123456789"
        assert ticket.source_dedupe_key
        assert "SPX123456789" in (ticket.required_action or "")
        assert ticket.ticket_no not in first.json().get("reply", "")

        events = db.execute(select(TicketEvent).where(TicketEvent.ticket_id == ticket.id, TicketEvent.event_type == EventType.ticket_created)).scalars().all()
        assert len(events) == 1
        assert "source_dedupe_key" in (events[0].payload_json or "")

        processed_again = dispatch_pending_webchat_handoff_snapshot_jobs(db, worker_id="worker-handoff-snapshot-test")
        assert processed_again == []
        assert len(db.execute(select(Ticket)).scalars().all()) == 1
        assert len(db.execute(select(TicketEvent).where(TicketEvent.ticket_id == ticket.id, TicketEvent.event_type == EventType.ticket_created)).scalars().all()) == 1
    finally:
        db.close()


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
    assert "recommended_agent_action" not in data


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
    assert _count_rows(Ticket) == 0
    assert _count_rows(TicketEvent) == 0


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
    assert _count_rows(Ticket) == 0
    assert _count_rows(TicketEvent) == 0


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
    assert _count_rows(Ticket) == 0


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
    assert _count_rows(Ticket) == 0
