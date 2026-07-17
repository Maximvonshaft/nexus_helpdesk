from __future__ import annotations

from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.enums import JobStatus, MessageStatus, SourceChannel
from app.models import BackgroundJob, TicketOutboundMessage
from app.services import queue_health
from app.utils.time import utc_now


def _session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'queue-health.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        future=True,
        expire_on_commit=False,
    )
    return engine, Session()


def test_empty_queues_are_business_ready(tmp_path, monkeypatch):
    engine, db = _session(tmp_path)
    monkeypatch.setenv("BUSINESS_QUEUE_MAX_READY_AGE_SECONDS", "300")
    try:
        result = queue_health.collect_queue_health(db)
    finally:
        db.close()
        engine.dispose()

    assert result["status"] == "ready"
    assert result["reason_codes"] == []
    assert result["contains_payloads"] is False
    assert result["background_jobs"]["counts"] == {}
    assert result["outbound"]["counts"] == {}


def test_stale_processing_and_old_pending_are_not_ready(tmp_path, monkeypatch):
    engine, db = _session(tmp_path)
    monkeypatch.setenv("BUSINESS_QUEUE_MAX_READY_AGE_SECONDS", "30")
    now = utc_now()
    try:
        db.add_all(
            [
                BackgroundJob(
                    queue_name="background",
                    job_type="test.pending",
                    payload_json='{"secret":"must-not-appear"}',
                    status=JobStatus.pending,
                    created_at=now - timedelta(minutes=5),
                    updated_at=now - timedelta(minutes=5),
                ),
                BackgroundJob(
                    queue_name="background",
                    job_type="test.processing",
                    payload_json='{"customer":"must-not-appear"}',
                    status=JobStatus.processing,
                    locked_at=now - timedelta(hours=1),
                    locked_by="expired-worker-token",
                    created_at=now - timedelta(hours=1),
                    updated_at=now - timedelta(hours=1),
                ),
                TicketOutboundMessage(
                    ticket_id=1,
                    channel=SourceChannel.email,
                    status=MessageStatus.pending,
                    body="private message must not appear",
                    provider_status="queued",
                    created_at=now - timedelta(minutes=5),
                    updated_at=now - timedelta(minutes=5),
                ),
                TicketOutboundMessage(
                    ticket_id=2,
                    channel=SourceChannel.email,
                    status=MessageStatus.processing,
                    body="another private message must not appear",
                    provider_status="processing",
                    locked_at=now - timedelta(hours=1),
                    locked_by="expired-outbound-token",
                    created_at=now - timedelta(hours=1),
                    updated_at=now - timedelta(hours=1),
                ),
            ]
        )
        db.commit()
        result = queue_health.collect_queue_health(db)
    finally:
        db.close()
        engine.dispose()

    assert result["status"] == "not_ready"
    assert {
        "background_jobs_stale_processing",
        "outbound_stale_processing",
        "background_jobs_ready_age_slo_breached",
        "outbound_ready_age_slo_breached",
    }.issubset(set(result["reason_codes"]))
    assert result["background_jobs"]["stale_processing"] == 1
    assert result["outbound"]["stale_processing"] == 1
    rendered = str(result)
    assert "must-not-appear" not in rendered
    assert "expired-worker-token" not in rendered
    assert "expired-outbound-token" not in rendered


def test_dead_items_degrade_but_do_not_fake_process_failure(tmp_path, monkeypatch):
    engine, db = _session(tmp_path)
    monkeypatch.setenv("BUSINESS_QUEUE_MAX_READY_AGE_SECONDS", "300")
    try:
        db.add(
            BackgroundJob(
                queue_name="background",
                job_type="test.dead",
                payload_json="{}",
                status=JobStatus.dead,
            )
        )
        db.commit()
        result = queue_health.collect_queue_health(db)
    finally:
        db.close()
        engine.dispose()

    assert result["status"] == "degraded"
    assert result["reason_codes"] == ["background_jobs_dead_present"]
    assert result["background_jobs"]["dead"] == 1
