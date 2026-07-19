from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import ServiceHeartbeat
from ..utils.time import ensure_utc, utc_now

WORKER_HEARTBEAT_SCHEMA = "nexus.worker-progress.v1"
WORKER_HEARTBEAT_PREFIX = "worker-progress"


@dataclass(frozen=True)
class WorkerProgress:
    worker_id: str
    queue: str
    status: str
    last_seen_at: datetime
    cycle_started_at: datetime | None
    last_success_at: datetime | None
    last_failure_at: datetime | None
    processed: int
    cycle_count: int
    failure_count: int
    error_type: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": WORKER_HEARTBEAT_SCHEMA,
            "worker_id": self.worker_id,
            "queue": self.queue,
            "status": self.status,
            "last_seen_at": ensure_utc(self.last_seen_at).isoformat(),
            "cycle_started_at": ensure_utc(self.cycle_started_at).isoformat() if self.cycle_started_at else None,
            "last_success_at": ensure_utc(self.last_success_at).isoformat() if self.last_success_at else None,
            "last_failure_at": ensure_utc(self.last_failure_at).isoformat() if self.last_failure_at else None,
            "processed": self.processed,
            "cycle_count": self.cycle_count,
            "failure_count": self.failure_count,
            "error_type": self.error_type,
            "contains_payloads": False,
        }


def worker_service_name(worker_id: str, queue: str) -> str:
    normalized_worker = str(worker_id or "").strip().lower()
    normalized_queue = str(queue or "").strip().lower()
    if not normalized_worker or not normalized_queue:
        raise ValueError("worker_progress_identity_required")
    value = f"{WORKER_HEARTBEAT_PREFIX}:{normalized_queue}:{normalized_worker}"
    if len(value) > 80:
        raise ValueError("worker_progress_identity_too_long")
    return value


def _details(row: ServiceHeartbeat | None) -> dict[str, Any]:
    value = row.details_json if row and isinstance(row.details_json, dict) else {}
    return dict(value)


def _write_progress(
    *,
    worker_id: str,
    queue: str,
    status: str,
    processed: int | None = None,
    error_type: str | None = None,
    at: datetime | None = None,
) -> WorkerProgress:
    observed = ensure_utc(at or utc_now())
    service_name = worker_service_name(worker_id, queue)
    db: Session = SessionLocal()
    try:
        row = (
            db.query(ServiceHeartbeat)
            .filter(ServiceHeartbeat.service_name == service_name)
            .with_for_update()
            .first()
        )
        prior = _details(row)
        cycle_count = int(prior.get("cycle_count") or 0)
        failure_count = int(prior.get("failure_count") or 0)
        cycle_started_at = prior.get("cycle_started_at")
        last_success_at = prior.get("last_success_at")
        last_failure_at = prior.get("last_failure_at")

        if status == "running":
            cycle_count += 1
            cycle_started_at = observed.isoformat()
        elif status == "healthy":
            last_success_at = observed.isoformat()
        elif status == "failed":
            failure_count += 1
            last_failure_at = observed.isoformat()

        details = {
            "schema": WORKER_HEARTBEAT_SCHEMA,
            "worker_id": worker_id,
            "queue": queue,
            "cycle_started_at": cycle_started_at,
            "last_success_at": last_success_at,
            "last_failure_at": last_failure_at,
            "processed": max(0, int(processed or 0)),
            "cycle_count": cycle_count,
            "failure_count": failure_count,
            "error_type": str(error_type or "").strip()[:120] or None,
            "contains_payloads": False,
        }
        if row is None:
            row = ServiceHeartbeat(
                service_name=service_name,
                instance_id=worker_id,
                status=status,
                details_json=details,
                last_seen_at=observed,
                updated_at=observed,
            )
            db.add(row)
        else:
            row.instance_id = worker_id
            row.status = status
            row.details_json = details
            row.last_seen_at = observed
            row.updated_at = observed
        db.commit()
        return _progress_from_row(row)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return ensure_utc(value)
    if isinstance(value, str) and value.strip():
        try:
            return ensure_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def _progress_from_row(row: ServiceHeartbeat) -> WorkerProgress:
    details = _details(row)
    return WorkerProgress(
        worker_id=str(details.get("worker_id") or row.instance_id or ""),
        queue=str(details.get("queue") or ""),
        status=str(row.status or "unknown"),
        last_seen_at=ensure_utc(row.last_seen_at),
        cycle_started_at=_parse_datetime(details.get("cycle_started_at")),
        last_success_at=_parse_datetime(details.get("last_success_at")),
        last_failure_at=_parse_datetime(details.get("last_failure_at")),
        processed=max(0, int(details.get("processed") or 0)),
        cycle_count=max(0, int(details.get("cycle_count") or 0)),
        failure_count=max(0, int(details.get("failure_count") or 0)),
        error_type=str(details.get("error_type") or "").strip() or None,
    )


def record_worker_cycle_started(worker_id: str, queue: str) -> WorkerProgress:
    return _write_progress(worker_id=worker_id, queue=queue, status="running")


def record_worker_cycle_succeeded(worker_id: str, queue: str, processed: int) -> WorkerProgress:
    return _write_progress(
        worker_id=worker_id,
        queue=queue,
        status="healthy",
        processed=processed,
    )


def record_worker_cycle_failed(worker_id: str, queue: str, error: BaseException) -> WorkerProgress:
    return _write_progress(
        worker_id=worker_id,
        queue=queue,
        status="failed",
        error_type=type(error).__name__,
    )


def read_worker_progress(db: Session, worker_id: str, queue: str) -> WorkerProgress | None:
    row = (
        db.query(ServiceHeartbeat)
        .filter(ServiceHeartbeat.service_name == worker_service_name(worker_id, queue))
        .first()
    )
    return _progress_from_row(row) if row else None
