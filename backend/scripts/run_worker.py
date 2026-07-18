from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal, db_context  # noqa: E402
from app.services.background_job_transaction_boundary import (  # noqa: E402
    dispatch_pending_background_jobs,
    dispatch_pending_webchat_ai_reply_jobs,
)
from app.services.outbound_dispatch_transaction_boundary import (  # noqa: E402
    dispatch_pending_messages,
)
from app.services.observability import (  # noqa: E402
    configure_logging,
    log_event,
    record_queue_snapshot,
    record_worker_poll,
    record_worker_result,
)
from app.services.queue_health import collect_queue_health  # noqa: E402
from app.services.webchat_ai_reconciler import (  # noqa: E402
    reconcile_webchat_ai_state,
)
from app.services.webchat_handoff_snapshot_worker import (  # noqa: E402
    dispatch_pending_webchat_handoff_snapshot_jobs,
)
from app.settings import get_settings  # noqa: E402

LOGGER = logging.getLogger(__name__)

settings = get_settings()
configure_logging(settings.log_json)

QUEUES = {
    "all",
    "outbound",
    "background",
    "webchat-ai",
    "handoff-snapshot",
}
_LAST_WEBCHAT_AI_RECONCILER_RUN_AT = 0.0
_LAST_QUEUE_DEPTH_SNAPSHOT_AT = 0.0
_QUEUE_DEPTH_LABELS: set[tuple[str, str]] = set()


def _is_sqlalchemy_session(db) -> bool:
    # Several legacy worker tests replace db_context() with a SimpleNamespace
    # fake to assert dispatch behavior. The WebChat handoff snapshot worker uses
    # BackgroundJob claiming and therefore requires a real SQLAlchemy Session.
    return (
        hasattr(db, "bind")
        and hasattr(db, "query")
        and hasattr(db, "commit")
    )


def _run_outbound(worker_id: str) -> int:
    if not settings.enable_outbound_dispatch:
        record_worker_result(worker_id, "outbound", "disabled", 1)
        return 0
    with db_context() as db:
        outbound = dispatch_pending_messages(db, worker_id=worker_id)
        if outbound:
            record_worker_result(
                worker_id,
                "outbound",
                "processed",
                len(outbound),
            )
        return len(outbound)


def _run_background(worker_id: str) -> int:
    with db_context() as db:
        jobs = dispatch_pending_background_jobs(db, worker_id=worker_id)
        if jobs:
            record_worker_result(
                worker_id,
                "background_job",
                "processed",
                len(jobs),
            )
        return len(jobs)


def _run_handoff_snapshot(worker_id: str) -> int:
    with db_context() as db:
        if _is_sqlalchemy_session(db):
            handoff_jobs = dispatch_pending_webchat_handoff_snapshot_jobs(
                db,
                worker_id=worker_id,
            )
        else:
            handoff_jobs = []
        if handoff_jobs:
            record_worker_result(
                worker_id,
                "webchat_handoff_snapshot",
                "processed",
                len(handoff_jobs),
            )
        return len(handoff_jobs)


def _webchat_ai_reconciler_interval_seconds() -> int:
    try:
        return max(
            5,
            int(
                getattr(
                    settings,
                    "webchat_ai_reconciler_interval_seconds",
                    30,
                )
                or 30
            ),
        )
    except (TypeError, ValueError):
        return 30


def _queue_depth_snapshot_interval_seconds() -> int:
    raw = os.getenv("QUEUE_METRICS_SNAPSHOT_INTERVAL_SECONDS", "15").strip()
    try:
        value = int(raw)
    except ValueError:
        LOGGER.warning(
            "queue_depth_snapshot_interval_invalid",
            extra={"event_payload": {"fallback_seconds": 15}},
        )
        return 15
    return max(5, min(300, value))


def _run_webchat_ai_reconciler_watchdog(worker_id: str) -> int:
    db = SessionLocal()
    started = time.monotonic()
    try:
        result = reconcile_webchat_ai_state(db)
        db.commit()
        processed = (
            int(result.get("cleared", 0) or 0)
            + int(result.get("failed", 0) or 0)
            + int(result.get("promoted", 0) or 0)
        )
        if processed:
            record_worker_result(
                worker_id,
                "webchat_ai_reconciler",
                "processed",
                processed,
            )
        if processed or int(result.get("timed_out", 0) or 0):
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
                        "elapsed_ms": int(
                            (time.monotonic() - started) * 1000
                        ),
                    }
                },
            )
        return processed
    except Exception:
        db.rollback()
        record_worker_result(
            worker_id,
            "webchat_ai_reconciler",
            "failed",
            1,
        )
        LOGGER.exception(
            "webchat_ai_reconciler_failed",
            extra={
                "event_payload": {
                    "worker_id": worker_id,
                    "elapsed_ms": int(
                        (time.monotonic() - started) * 1000
                    ),
                }
            },
        )
        return 0
    finally:
        db.close()


def _run_webchat_ai(worker_id: str) -> int:
    global _LAST_WEBCHAT_AI_RECONCILER_RUN_AT
    processed = 0
    with db_context() as db:
        jobs = dispatch_pending_webchat_ai_reply_jobs(
            db,
            limit=1,
            worker_id=worker_id,
        )
        if jobs:
            record_worker_result(
                worker_id,
                "webchat_ai_reply",
                "processed",
                len(jobs),
            )
        processed += len(jobs)

    if not bool(
        getattr(settings, "webchat_ai_reconciler_enabled", True)
    ):
        record_worker_result(
            worker_id,
            "webchat_ai_reconciler",
            "disabled",
            1,
        )
        return processed

    now = time.monotonic()
    if (
        now - _LAST_WEBCHAT_AI_RECONCILER_RUN_AT
        >= _webchat_ai_reconciler_interval_seconds()
    ):
        _LAST_WEBCHAT_AI_RECONCILER_RUN_AT = now
        processed += _run_webchat_ai_reconciler_watchdog(worker_id)
    return processed


def _record_queue_depth_snapshot_if_due(
    worker_id: str,
    *,
    queue: str,
) -> None:
    """Publish real database queue counts from one designated Worker only."""
    global _LAST_QUEUE_DEPTH_SNAPSHOT_AT, _QUEUE_DEPTH_LABELS

    # The controlled topology always has one dedicated background Worker.
    # Sampling from every Worker would multiply a multiprocess Gauge.
    if queue != "background":
        return
    now = time.monotonic()
    if (
        now - _LAST_QUEUE_DEPTH_SNAPSHOT_AT
        < _queue_depth_snapshot_interval_seconds()
    ):
        return
    _LAST_QUEUE_DEPTH_SNAPSHOT_AT = now

    db = SessionLocal()
    try:
        snapshot = collect_queue_health(db)
        current_labels: set[tuple[str, str]] = set()
        for queue_name, statuses in snapshot["background_jobs"][
            "counts"
        ].items():
            metric_name = f"background:{queue_name}"[:80]
            for status_name, count in statuses.items():
                label = (metric_name, str(status_name)[:80])
                current_labels.add(label)
                record_queue_snapshot(label[0], label[1], int(count))
        for channel_name, statuses in snapshot["outbound"]["counts"].items():
            metric_name = f"outbound:{channel_name}"[:80]
            for status_name, count in statuses.items():
                label = (metric_name, str(status_name)[:80])
                current_labels.add(label)
                record_queue_snapshot(label[0], label[1], int(count))
        aggregate = {
            ("background:all", "stale_processing"): int(
                snapshot["background_jobs"]["stale_processing"]
            ),
            ("outbound:all", "stale_processing"): int(
                snapshot["outbound"]["stale_processing"]
            ),
        }
        for label, count in aggregate.items():
            current_labels.add(label)
            record_queue_snapshot(label[0], label[1], count)
        for missing in _QUEUE_DEPTH_LABELS - current_labels:
            record_queue_snapshot(missing[0], missing[1], 0)
        _QUEUE_DEPTH_LABELS = current_labels
    except Exception:
        record_worker_result(
            worker_id,
            "queue_depth_snapshot",
            "failed",
            1,
        )
        LOGGER.exception(
            "queue_depth_snapshot_failed",
            extra={"event_payload": {"worker_id": worker_id}},
        )
    finally:
        db.close()


def run_queue_once(worker_id: str, queue: str) -> int:
    if queue not in QUEUES:
        raise ValueError(f"unsupported worker queue: {queue}")
    record_worker_poll(worker_id)
    processed = 0
    if queue in {"all", "outbound"}:
        processed += _run_outbound(worker_id)
    if queue in {"all", "background"}:
        processed += _run_background(worker_id)
    if queue in {"all", "handoff-snapshot"}:
        processed += _run_handoff_snapshot(worker_id)
    if queue in {"all", "webchat-ai"}:
        processed += _run_webchat_ai(worker_id)
    _record_queue_depth_snapshot_if_due(worker_id, queue=queue)
    if processed > 0 or queue != "webchat-ai":
        log_event(
            20,
            "worker_cycle_complete",
            worker_id=worker_id,
            queue=queue,
            processed=processed,
        )
    return processed


def run_once(worker_id: str) -> int:
    return run_queue_once(worker_id, "all")


def _sleep_seconds_for_queue(queue: str, processed: int) -> float:
    if processed > 0:
        return float(
            settings.webchat_ai_worker_busy_poll_seconds
            if queue == "webchat-ai"
            else 0.2
        )
    if queue == "webchat-ai":
        return float(settings.webchat_ai_worker_poll_seconds)
    return float(settings.worker_poll_seconds)


def _install_shutdown_handlers() -> None:
    def request_shutdown(signum, _frame) -> None:  # noqa: ANN001
        log_event(
            20,
            "worker_shutdown_requested",
            signal=signal.Signals(signum).name,
        )
        # SystemExit runs Python atexit handlers, which remove this worker's
        # namespaced live-Gauge files from the shared canonical registry.
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run isolated NexusDesk background worker queues"
    )
    parser.add_argument("--worker-id", default="worker-main")
    parser.add_argument("--queue", choices=sorted(QUEUES), default="all")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    worker_id = getattr(args, "worker_id", "worker-main")
    queue = getattr(args, "queue", "all")
    once = bool(getattr(args, "once", False))

    while True:
        processed = run_queue_once(worker_id, queue)
        if once:
            print(f"processed={processed}")
            return 0
        time.sleep(_sleep_seconds_for_queue(queue, processed))


if __name__ == "__main__":
    _install_shutdown_handlers()
    raise SystemExit(main())
