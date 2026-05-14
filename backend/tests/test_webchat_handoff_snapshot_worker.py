from __future__ import annotations

import os

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_handoff_snapshot_worker_tests.db")

from sqlalchemy import delete, select

from app.db import Base, SessionLocal, engine
from app.enums import ConversationState, EventType, JobStatus, SourceChannel, TicketStatus
from app.models import BackgroundJob, Ticket, TicketEvent
from app.services.webchat_handoff_snapshot_service import (
    WEBCHAT_HANDOFF_SNAPSHOT_JOB,
    build_handoff_snapshot_payload,
    enqueue_webchat_handoff_snapshot_job,
)
from app.services.webchat_handoff_snapshot_worker import dispatch_pending_webchat_handoff_snapshot_jobs


def setup_function():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        db.execute(delete(TicketEvent))
        db.execute(delete(Ticket))
        db.execute(delete(BackgroundJob))
        db.commit()
    finally:
        db.close()


def _snapshot(client_message_id: str = "client-handoff-worker-1") -> dict:
    return build_handoff_snapshot_payload(
        tenant_key="default",
        channel_key="website",
        session_id="session-handoff-worker-1",
        client_message_id=client_message_id,
        customer_last_message="My parcel SPX123456789 has no update. Please help.",
        ai_reply="I’ll route this to a support specialist. Please keep tracking number SPX123456789 as the reference.",
        intent="handoff",
        tracking_number="SPX123456789",
        handoff_reason="tracking_unresolved",
        recommended_agent_action="Check shipment SPX123456789 and reply with verified ETA if no update is available.",
        recent_context=[{"role": "customer", "text": "Where is SPX123456789?"}],
        visitor={"email": "visitor@example.test"},
    )


def test_worker_consumes_handoff_snapshot_and_creates_exactly_one_human_ticket():
    db = SessionLocal()
    try:
        snapshot = _snapshot()
        first_job = enqueue_webchat_handoff_snapshot_job(db, snapshot=snapshot)
        second_job = enqueue_webchat_handoff_snapshot_job(db, snapshot=snapshot)
        db.commit()

        assert first_job.id == second_job.id
        jobs = db.execute(select(BackgroundJob).where(BackgroundJob.job_type == WEBCHAT_HANDOFF_SNAPSHOT_JOB)).scalars().all()
        assert len(jobs) == 1
        assert jobs[0].status == JobStatus.pending

        processed = dispatch_pending_webchat_handoff_snapshot_jobs(db, worker_id="worker-handoff-snapshot-test")
        assert len(processed) == 1

        jobs_after = db.execute(select(BackgroundJob).where(BackgroundJob.job_type == WEBCHAT_HANDOFF_SNAPSHOT_JOB)).scalars().all()
        assert len(jobs_after) == 1
        assert jobs_after[0].status == JobStatus.done

        tickets = db.execute(select(Ticket).where(Ticket.source_dedupe_key == snapshot["source_dedupe_key"])).scalars().all()
        assert len(tickets) == 1
        ticket = tickets[0]
        assert ticket.source_channel == SourceChannel.web_chat
        assert ticket.status == TicketStatus.pending_assignment
        assert ticket.conversation_state == ConversationState.human_review_required
        assert ticket.tracking_number == "SPX123456789"
        assert ticket.source_dedupe_key == snapshot["source_dedupe_key"]
        assert "SPX123456789" in (ticket.required_action or "")
        assert ticket.preferred_reply_contact == "visitor@example.test"

        events = db.execute(
            select(TicketEvent).where(
                TicketEvent.ticket_id == ticket.id,
                TicketEvent.event_type == EventType.ticket_created,
            )
        ).scalars().all()
        assert len(events) == 1
        assert "SPX123456789" in (events[0].payload_json or "")
        assert snapshot["source_dedupe_key"] in (events[0].payload_json or "")

        processed_again = dispatch_pending_webchat_handoff_snapshot_jobs(db, worker_id="worker-handoff-snapshot-test")
        assert processed_again == []
        assert len(db.execute(select(Ticket).where(Ticket.source_dedupe_key == snapshot["source_dedupe_key"])).scalars().all()) == 1
        assert len(db.execute(select(TicketEvent).where(TicketEvent.ticket_id == ticket.id, TicketEvent.event_type == EventType.ticket_created)).scalars().all()) == 1
    finally:
        db.close()
