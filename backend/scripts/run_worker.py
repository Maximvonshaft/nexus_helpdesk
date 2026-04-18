from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import db_context  # noqa: E402
from app.services.background_jobs import dispatch_pending_background_jobs  # noqa: E402
from app.services.message_dispatch import dispatch_pending_messages  # noqa: E402
from app.services.observability import configure_logging, log_event, record_queue_snapshot, record_worker_poll, record_worker_result  # noqa: E402
from app.settings import get_settings  # noqa: E402

settings = get_settings()
configure_logging(settings.log_json)


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
    with db_context() as db:
        jobs = dispatch_pending_background_jobs(db, worker_id=worker_id)
        processed += len(jobs)
        if jobs:
            record_worker_result(worker_id, "background_job", "processed", len(jobs))
        record_queue_snapshot("background_job", "processed", len(jobs))
    log_event(20, "worker_cycle_complete", worker_id=worker_id, processed=processed)
    return processed


def main() -> int:
    parser = argparse.ArgumentParser(description='Run background workers for outbound dispatch and background jobs')
    parser.add_argument('--worker-id', default='worker-main')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()

    while True:
        processed = run_once(args.worker_id)
        if args.once:
            print(f'processed={processed}')
            return 0
        time.sleep(settings.worker_poll_seconds if processed == 0 else 0.2)


if __name__ == '__main__':
    raise SystemExit(main())
