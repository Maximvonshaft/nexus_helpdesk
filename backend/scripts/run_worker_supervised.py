from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_worker  # noqa: E402
from app.services.worker_progress import (  # noqa: E402
    record_worker_cycle_failed,
    record_worker_cycle_started,
    record_worker_cycle_succeeded,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the canonical Nexus Worker with durable progress supervision"
    )
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--queue", choices=sorted(run_worker.QUEUES), required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    while True:
        record_worker_cycle_started(args.worker_id, args.queue)
        try:
            processed = run_worker.run_queue_once(args.worker_id, args.queue)
        except BaseException as exc:
            record_worker_cycle_failed(args.worker_id, args.queue, exc)
            raise
        record_worker_cycle_succeeded(args.worker_id, args.queue, processed)
        if args.once:
            print(f"processed={processed}")
            return 0
        time.sleep(run_worker._sleep_seconds_for_queue(args.queue, processed))


if __name__ == "__main__":
    run_worker._install_shutdown_handlers()
    raise SystemExit(main())
