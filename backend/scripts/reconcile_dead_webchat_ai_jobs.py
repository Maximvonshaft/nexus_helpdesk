#!/usr/bin/env python3
"""Safely reconcile historical dead WebChat AI jobs.

Dry-run is the default. ``--apply`` is source-bound and processes each job in an
independent transaction. The canonical terminal-outcome service decides whether
the job already has a reply, is superseded by a newer/human outcome, or requires
one idempotent customer-safe fallback. No row is deleted.
"""

from __future__ import annotations

import argparse
import json
import os
import re

from app.db import SessionLocal
from app.enums import JobStatus
from app.models import BackgroundJob
from app.services.background_jobs import WEBCHAT_AI_REPLY_JOB
from app.services.webchat_ai_terminal_job_service import finalize_dead_webchat_ai_job

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _status_value(value: object) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _candidate_ids(limit: int) -> list[int]:
    with SessionLocal() as db:
        return [
            int(row[0])
            for row in (
                db.query(BackgroundJob.id)
                .filter(
                    BackgroundJob.job_type == WEBCHAT_AI_REPLY_JOB,
                    BackgroundJob.status == JobStatus.dead,
                )
                .order_by(BackgroundJob.id.asc())
                .limit(limit)
                .all()
            )
        ]


def _require_source_binding(expected: str) -> str:
    runtime_sha = str(os.getenv("GIT_SHA") or "").strip().lower()
    expected = str(expected or "").strip().lower()
    if not _SHA_RE.fullmatch(expected):
        raise SystemExit("--expected-source-sha must be a 40-character lowercase SHA")
    if runtime_sha != expected:
        raise SystemExit("runtime GIT_SHA does not match --expected-source-sha")
    return runtime_sha


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-source-sha", default="")
    args = parser.parse_args()

    limit = max(1, min(int(args.limit or 100), 1000))
    source_sha = None
    if args.apply:
        source_sha = _require_source_binding(args.expected_source_sha)

    job_ids = _candidate_ids(limit)
    result = {
        "schema": "nexus.webchat-ai-dead-job-reconciliation.v1",
        "mode": "apply" if args.apply else "dry_run",
        "source_sha": source_sha,
        "selected": len(job_ids),
        "resolved": 0,
        "failed": 0,
        "remaining_dead": 0,
        "job_ids": job_ids,
        "failure_job_ids": [],
    }
    if not args.apply:
        print(json.dumps(result, sort_keys=True))
        return 0

    for job_id in job_ids:
        with SessionLocal() as db:
            try:
                job = (
                    db.query(BackgroundJob)
                    .filter(
                        BackgroundJob.id == job_id,
                        BackgroundJob.job_type == WEBCHAT_AI_REPLY_JOB,
                        BackgroundJob.status == JobStatus.dead,
                    )
                    .with_for_update()
                    .one_or_none()
                )
                if job is None:
                    db.rollback()
                    continue
                finalize_dead_webchat_ai_job(db, job)
                if _status_value(job.status) != JobStatus.done.value:
                    raise RuntimeError("dead job did not reach a resolved terminal state")
                db.commit()
                result["resolved"] += 1
            except Exception:
                db.rollback()
                result["failed"] += 1
                result["failure_job_ids"].append(job_id)

    result["remaining_dead"] = len(_candidate_ids(1000))
    print(json.dumps(result, sort_keys=True))
    return 1 if result["failed"] or result["remaining_dead"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
