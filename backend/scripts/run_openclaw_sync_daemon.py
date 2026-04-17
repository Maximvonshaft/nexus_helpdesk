from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import db_context  # noqa: E402
from app.services.background_jobs import dispatch_pending_sync_jobs  # noqa: E402
from app.services.observability import configure_logging, log_event, record_worker_poll, record_worker_result  # noqa: E402
from app.settings import get_settings  # noqa: E402

settings = get_settings()
configure_logging(settings.log_json)


def run_once(worker_id: str) -> int:
    record_worker_poll(worker_id)
    with db_context() as db:
        jobs = dispatch_pending_sync_jobs(db, worker_id=worker_id)
    count = len(jobs)
    if count:
        record_worker_result(worker_id, "openclaw_sync_job", "processed", count)
    log_event(20, "openclaw_sync_cycle_complete", worker_id=worker_id, processed=count)
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description="Run dedicated OpenClaw transcript sync daemon")
    parser.add_argument("--worker-id", default="worker-openclaw-sync")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    while True:
        processed = run_once(args.worker_id)
        if args.once:
            print(f"processed={processed}")
            return 0
        time.sleep(10 if processed == 0 else 0.5)


if __name__ == "__main__":
    raise SystemExit(main())
