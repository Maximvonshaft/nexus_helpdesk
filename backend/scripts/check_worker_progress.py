from __future__ import annotations

import json
import os
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import SessionLocal  # noqa: E402
from app.services.worker_progress import read_worker_progress  # noqa: E402
from app.utils.time import ensure_utc, utc_now  # noqa: E402


def _positive_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name}_invalid") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name}_out_of_range")
    return value


def main() -> int:
    worker_id = os.getenv("NEXUS_WORKER_ID", "").strip()
    queue = os.getenv("NEXUS_WORKER_QUEUE", "").strip()
    if not worker_id or not queue:
        print(json.dumps({"ok": False, "reason": "worker_identity_missing"}, sort_keys=True))
        return 2

    max_success_age = _positive_int(
        "WORKER_PROGRESS_MAX_SUCCESS_AGE_SECONDS",
        90,
        minimum=10,
        maximum=3600,
    )
    max_cycle_age = _positive_int(
        "WORKER_PROGRESS_MAX_CYCLE_AGE_SECONDS",
        300,
        minimum=30,
        maximum=7200,
    )
    db = SessionLocal()
    try:
        progress = read_worker_progress(db, worker_id, queue)
    finally:
        db.close()
    if progress is None:
        print(json.dumps({"ok": False, "reason": "worker_progress_missing"}, sort_keys=True))
        return 1

    now = utc_now()
    success_fresh = bool(
        progress.last_success_at
        and ensure_utc(progress.last_success_at) >= now - timedelta(seconds=max_success_age)
    )
    running_fresh = bool(
        progress.status == "running"
        and progress.cycle_started_at
        and ensure_utc(progress.cycle_started_at) >= now - timedelta(seconds=max_cycle_age)
    )
    healthy = progress.status == "healthy" and success_fresh
    ok = healthy or (running_fresh and (success_fresh or progress.cycle_count <= 1))
    payload = {
        "ok": ok,
        "schema": "nexus.worker-progress-health.v1",
        "worker_id": worker_id,
        "queue": queue,
        "status": progress.status,
        "last_seen_at": ensure_utc(progress.last_seen_at).isoformat(),
        "last_success_at": ensure_utc(progress.last_success_at).isoformat() if progress.last_success_at else None,
        "cycle_started_at": ensure_utc(progress.cycle_started_at).isoformat() if progress.cycle_started_at else None,
        "success_fresh": success_fresh,
        "running_fresh": running_fresh,
        "cycle_count": progress.cycle_count,
        "failure_count": progress.failure_count,
        "contains_payloads": False,
    }
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
