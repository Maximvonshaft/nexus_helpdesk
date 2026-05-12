from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import db_context  # noqa: E402
from app.services.background_jobs_policy import dispatch_pending_background_jobs  # noqa: E402
from app.services.message_dispatch import dispatch_pending_messages  # noqa: E402
from app.services.observability import configure_logging, log_event, record_queue_snapshot, record_worker_poll, record_worker_result  # noqa: E402
from app.services.openclaw_bridge import sync_openclaw_inbound_conversations_once  # noqa: E402
from app.services.webchat_handoff_snapshot_worker import dispatch_pending_webchat_handoff_snapshot_jobs  # noqa: E402
from app.settings import get_settings  # noqa: E402
import logging
from app.db import SessionLocal
from app.services.webchat_ai_reconciler import reconcile_webchat_ai_state

LOGGER = logging.getLogger(__name__)

settings = get_settings()
configure_logging(settings.log_json)


def _is_sqlalchemy_session(db) -> bool:
    # Several legacy worker tests replace db_context() with a SimpleNamespace
    # fake to assert dispatch behavior. The WebChat handoff snapshot worker uses
    # BackgroundJob claiming and therefore requires a real SQLAlchemy Session.
    return hasattr(db, "bind") and hasattr(db, "query") and hasattr(db, "commit")


def run_once(worker_id: str) -> int:
    record_worker_poll(worker_id)
    processed = 0
    if settings.enable_outbound_dispatch:
        with db_context() as db:
            outbound = dispatch_pending_messages(db, worker_id=worker_id)
            processed += len(outbound)
            if outbound:
                record_worker_result(worker_id, "outbound", "processed", len(outbound))
            record_queue_snapshot("outbound", "processed", len(outbound))
    else:
        record_queue_snapshot("outbound", "disabled", 0)
    if settings.openclaw_sync_enabled and settings.openclaw_inbound_auto_sync_enabled:
        log_event(20, "openclaw_inbound_sync_started", worker_id=worker_id)
        started_at = time.perf_counter()
        try:
            with db_context() as db:
                inbound = sync_openclaw_inbound_conversations_once(db, source='default')
                processed += int(inbound.get('synced_conversations', 0))
                if inbound.get('synced_conversations'):
                    record_worker_result(worker_id, "openclaw_inbound", "processed", int(inbound.get('synced_conversations', 0)))
                record_queue_snapshot("openclaw_inbound", "processed", int(inbound.get('synced_conversations', 0)))
                log_event(
                    20,
                    "openclaw_inbound_sync_completed",
                    worker_id=worker_id,
                    conversations_seen=int(inbound.get('conversations_seen', 0)),
                    tickets_created=int(inbound.get('tickets_created', 0)),
                    messages_inserted=int(inbound.get('messages_inserted', 0)),
                    unresolved_events=int(inbound.get('unresolved_events', 0)),
                    duration_ms=int((time.perf_counter() - started_at) * 1000),
                )
        except Exception as exc:
            log_event(40, "openclaw_inbound_cycle_failed", worker_id=worker_id, error=str(exc))
            record_queue_snapshot("openclaw_inbound", "failed", 0)
    else:
        record_queue_snapshot("openclaw_inbound", "disabled", 0)
    with db_context() as db:
        jobs = dispatch_pending_background_jobs(db, worker_id=worker_id)
        processed += len(jobs)
        if jobs:
            record_worker_result(worker_id, "background_job", "processed", len(jobs))
        record_queue_snapshot("background_job", "processed", len(jobs))
    with db_context() as db:
        if _is_sqlalchemy_session(db):
            handoff_jobs = dispatch_pending_webchat_handoff_snapshot_jobs(db, worker_id=worker_id)
        else:
            handoff_jobs = []
        processed += len(handoff_jobs)
        if handoff_jobs:
            record_worker_result(worker_id, "webchat_handoff_snapshot", "processed", len(handoff_jobs))
        record_queue_snapshot("webchat_handoff_snapshot", "processed", len(handoff_jobs))
    log_event(20, "worker_cycle_complete", worker_id=worker_id, processed=processed)
    return processed


def main() -> int:
    parser = argparse.ArgumentParser(description='Run background workers for outbound dispatch and background jobs')
    parser.add_argument('--worker-id', default='worker-main')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()

    webchat_ai_reconciler_interval_seconds = _webchat_ai_reconciler_interval_seconds()
    next_webchat_ai_reconciler_run_at = 0.0
    webchat_ai_reconciler_worker_id = str(args.worker_id)
    while True:
        webchat_ai_reconciler_now = time.monotonic()
        if (
            _should_run_webchat_ai_reconciler(webchat_ai_reconciler_worker_id)
            and webchat_ai_reconciler_now >= next_webchat_ai_reconciler_run_at
        ):
            _run_webchat_ai_reconciler_watchdog(webchat_ai_reconciler_worker_id)
            next_webchat_ai_reconciler_run_at = (
                webchat_ai_reconciler_now + webchat_ai_reconciler_interval_seconds
            )
        processed = run_once(args.worker_id)
        if args.once:
            print(f'processed={processed}')
            return 0
        time.sleep(settings.worker_poll_seconds if processed == 0 else 0.2)



def _webchat_ai_reconciler_interval_seconds() -> int:
    try:
        return max(5, int(getattr(settings, "webchat_ai_reconciler_interval_seconds", 30)))
    except (TypeError, ValueError):
        return 30


def _should_run_webchat_ai_reconciler(worker_id: str) -> bool:
    return (
        worker_id == "worker-main"
        and bool(getattr(settings, "webchat_ai_reconciler_enabled", True))
    )


def _run_webchat_ai_reconciler_watchdog(worker_id: str) -> None:
    db = SessionLocal()
    started = time.monotonic()
    try:
        result = reconcile_webchat_ai_state(db)
        db.commit()
        LOGGER.info(
            "webchat_ai_reconciler_watchdog_completed",
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
    except Exception:
        db.rollback()
        LOGGER.exception(
            "webchat_ai_reconciler_watchdog_failed",
            extra={
                "event_payload": {
                    "worker_id": worker_id,
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                }
            },
        )
    finally:
        db.close()

if __name__ == '__main__':
    raise SystemExit(main())
