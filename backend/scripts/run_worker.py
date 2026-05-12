from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal, db_context  # noqa: E402
from app.services.background_jobs import dispatch_pending_background_jobs  # noqa: E402
from app.services.message_dispatch import dispatch_pending_messages  # noqa: E402
from app.services.observability import configure_logging, log_event, record_queue_snapshot, record_worker_poll, record_worker_result  # noqa: E402
from app.services.openclaw_bridge import sync_openclaw_inbound_conversations_once  # noqa: E402
from app.services.webchat_ai_reconciler import reconcile_webchat_ai_state  # noqa: E402
from app.services.webchat_handoff_snapshot_worker import dispatch_pending_webchat_handoff_snapshot_jobs  # noqa: E402
from app.settings import get_settings  # noqa: E402

LOGGER = logging.getLogger(__name__)

settings = get_settings()
configure_logging(settings.log_json)

QUEUES = {"all", "outbound", "background", "webchat-ai", "handoff-snapshot", "openclaw-inbound"}


def _is_sqlalchemy_session(db) -> bool:
    # Several legacy worker tests replace db_context() with a SimpleNamespace
    # fake to assert dispatch behavior. The WebChat handoff snapshot worker uses
    # BackgroundJob claiming and therefore requires a real SQLAlchemy Session.
    return hasattr(db, "bind") and hasattr(db, "query") and hasattr(db, "commit")


def _run_outbound(worker_id: str) -> int:
    if not settings.enable_outbound_dispatch:
        record_queue_snapshot("outbound", "disabled", 0)
        return 0
    with db_context() as db:
        outbound = dispatch_pending_messages(db, worker_id=worker_id)
        if outbound:
            record_worker_result(worker_id, "outbound", "processed", len(outbound))
        record_queue_snapshot("outbound", "processed", len(outbound))
        return len(outbound)


def _run_openclaw_inbound(worker_id: str) -> int:
    if not (settings.openclaw_sync_enabled and settings.openclaw_inbound_auto_sync_enabled):
        record_queue_snapshot("openclaw_inbound", "disabled", 0)
        return 0
    log_event(20, "openclaw_inbound_sync_started", worker_id=worker_id)
    started_at = time.perf_counter()
    try:
        with db_context() as db:
            inbound = sync_openclaw_inbound_conversations_once(db, source="default")
            count = int(inbound.get("synced_conversations", 0))
            if count:
                record_worker_result(worker_id, "openclaw_inbound", "processed", count)
            record_queue_snapshot("openclaw_inbound", "processed", count)
            log_event(
                20,
                "openclaw_inbound_sync_completed",
                worker_id=worker_id,
                conversations_seen=int(inbound.get("conversations_seen", 0)),
                tickets_created=int(inbound.get("tickets_created", 0)),
                messages_inserted=int(inbound.get("messages_inserted", 0)),
                unresolved_events=int(inbound.get("unresolved_events", 0)),
                duration_ms=int((time.perf_counter() - started_at) * 1000),
            )
            return count
    except Exception as exc:
        log_event(40, "openclaw_inbound_cycle_failed", worker_id=worker_id, error=str(exc))
        record_queue_snapshot("openclaw_inbound", "failed", 0)
        return 0


def _run_background(worker_id: str) -> int:
    with db_context() as db:
        jobs = dispatch_pending_background_jobs(db, worker_id=worker_id)
        if jobs:
            record_worker_result(worker_id, "background_job", "processed", len(jobs))
        record_queue_snapshot("background_job", "processed", len(jobs))
        return len(jobs)


def _run_handoff_snapshot(worker_id: str) -> int:
    with db_context() as db:
        if _is_sqlalchemy_session(db):
            handoff_jobs = dispatch_pending_webchat_handoff_snapshot_jobs(db, worker_id=worker_id)
        else:
            handoff_jobs = []
        if handoff_jobs:
            record_worker_result(worker_id, "webchat_handoff_snapshot", "processed", len(handoff_jobs))
        record_queue_snapshot("webchat_handoff_snapshot", "processed", len(handoff_jobs))
        return len(handoff_jobs)


def _webchat_ai_reconciler_interval_seconds() -> int:
    try:
        return max(5, int(getattr(settings, "webchat_ai_reconciler_interval_seconds", 30) or 30))
    except (TypeError, ValueError):
        return 30


def _should_run_webchat_ai_reconciler(worker_id: str) -> bool:
    return bool(getattr(settings, "webchat_ai_reconciler_enabled", True)) and worker_id == "worker-main"


def _run_webchat_ai_reconciler_watchdog(worker_id: str) -> int:
    db = SessionLocal()
    started = time.monotonic()
    try:
        result = reconcile_webchat_ai_state(db)
        db.commit()
        processed = int(result.get("cleared", 0) or 0) + int(result.get("failed", 0) or 0) + int(result.get("promoted", 0) or 0)
        record_queue_snapshot("webchat_ai_reconciler", "processed", processed)
        LOGGER.info(
            "webchat_ai_reconciler_completed",
            extra={
                "event_payload": {
                    "worker_id": worker_id,
                    "inspected": result.get("inspected"),
                    "cleared": result.get("cleared"),
                    "failed": result.get("failed"),
                    "promoted": result.get("promoted"),
                    "timed_out": result.get("timed_out"),
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                }
            },
        )
        return processed
    except Exception:
        db.rollback()
        record_queue_snapshot("webchat_ai_reconciler", "failed", 0)
        LOGGER.exception(
            "webchat_ai_reconciler_failed",
            extra={"event_payload": {"worker_id": worker_id, "elapsed_ms": int((time.monotonic() - started) * 1000)}},
        )
        return 0
    finally:
        db.close()


def _run_webchat_ai(worker_id: str) -> int:
    if not bool(getattr(settings, "webchat_ai_reconciler_enabled", True)):
        record_queue_snapshot("webchat_ai_reconciler", "disabled", 0)
        return 0
    return _run_webchat_ai_reconciler_watchdog(worker_id)


def run_queue_once(worker_id: str, queue: str) -> int:
    if queue not in QUEUES:
        raise ValueError(f"unsupported worker queue: {queue}")
    record_worker_poll(worker_id)
    processed = 0
    if queue in {"all", "outbound"}:
        processed += _run_outbound(worker_id)
    if queue in {"all", "openclaw-inbound"}:
        processed += _run_openclaw_inbound(worker_id)
    if queue in {"all", "background"}:
        processed += _run_background(worker_id)
    if queue in {"all", "handoff-snapshot"}:
        processed += _run_handoff_snapshot(worker_id)
    if queue in {"webchat-ai"}:
        processed += _run_webchat_ai(worker_id)
    log_event(20, "worker_cycle_complete", worker_id=worker_id, queue=queue, processed=processed)
    return processed


def run_once(worker_id: str) -> int:
    return run_queue_once(worker_id, "all")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run isolated NexusDesk background worker queues")
    parser.add_argument("--worker-id", default="worker-main")
    parser.add_argument("--queue", choices=sorted(QUEUES), default="all")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    worker_id = getattr(args, "worker_id", "worker-main")
    queue = getattr(args, "queue", "all")
    once = bool(getattr(args, "once", False))

    while True:
        if queue == "all":
            if _should_run_webchat_ai_reconciler(worker_id):
                _run_webchat_ai_reconciler_watchdog(worker_id)
            processed = run_once(worker_id)
        else:
            processed = run_queue_once(worker_id, queue)
        if once:
            print(f"processed={processed}")
            return 0
        time.sleep(settings.worker_poll_seconds if processed == 0 else 0.2)


if __name__ == "__main__":
    raise SystemExit(main())
