from __future__ import annotations

import os
from collections import defaultdict
from datetime import timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..enums import JobStatus, MessageStatus
from ..models import BackgroundJob, TicketOutboundMessage
from ..settings import get_settings
from ..utils.time import utc_now

settings = get_settings()


def _bounded_seconds(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise RuntimeError(f"{name}_invalid") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name}_out_of_range")
    return value


def _age_ms(value, *, now) -> int | None:
    if value is None:
        return None
    return max(0, int((now - value).total_seconds() * 1000))


def collect_queue_health(db: Session) -> dict[str, Any]:
    """Collect bounded queue state without payloads, message bodies or PII."""
    now = utc_now()
    maximum_ready_age_seconds = _bounded_seconds(
        "BUSINESS_QUEUE_MAX_READY_AGE_SECONDS",
        300,
        minimum=10,
        maximum=86400,
    )
    job_stale_before = now - timedelta(seconds=settings.job_lock_seconds)
    outbound_stale_before = now - timedelta(seconds=settings.outbox_lock_seconds)

    job_counts: dict[str, dict[str, int]] = defaultdict(dict)
    for queue_name, status_value, count in (
        db.query(BackgroundJob.queue_name, BackgroundJob.status, func.count(BackgroundJob.id))
        .group_by(BackgroundJob.queue_name, BackgroundJob.status)
        .all()
    ):
        status_key = status_value.value if hasattr(status_value, "value") else str(status_value)
        job_counts[str(queue_name)][status_key] = int(count)

    job_oldest_pending = (
        db.query(func.min(BackgroundJob.created_at))
        .filter(BackgroundJob.status == JobStatus.pending)
        .scalar()
    )
    stale_jobs = int(
        db.query(func.count(BackgroundJob.id))
        .filter(
            BackgroundJob.status == JobStatus.processing,
            (BackgroundJob.locked_at.is_(None))
            | (BackgroundJob.locked_at < job_stale_before),
        )
        .scalar()
        or 0
    )

    outbound_counts: dict[str, dict[str, int]] = defaultdict(dict)
    for channel_value, status_value, count in (
        db.query(
            TicketOutboundMessage.channel,
            TicketOutboundMessage.status,
            func.count(TicketOutboundMessage.id),
        )
        .group_by(TicketOutboundMessage.channel, TicketOutboundMessage.status)
        .all()
    ):
        channel_key = channel_value.value if hasattr(channel_value, "value") else str(channel_value)
        status_key = status_value.value if hasattr(status_value, "value") else str(status_value)
        outbound_counts[channel_key][status_key] = int(count)

    outbound_oldest_pending = (
        db.query(func.min(TicketOutboundMessage.created_at))
        .filter(TicketOutboundMessage.status == MessageStatus.pending)
        .scalar()
    )
    stale_outbound = int(
        db.query(func.count(TicketOutboundMessage.id))
        .filter(
            TicketOutboundMessage.status == MessageStatus.processing,
            (TicketOutboundMessage.locked_at.is_(None))
            | (TicketOutboundMessage.locked_at < outbound_stale_before),
        )
        .scalar()
        or 0
    )

    oldest_job_age_ms = _age_ms(job_oldest_pending, now=now)
    oldest_outbound_age_ms = _age_ms(outbound_oldest_pending, now=now)
    maximum_ready_age_ms = maximum_ready_age_seconds * 1000
    dead_jobs = sum(values.get(JobStatus.dead.value, 0) for values in job_counts.values())
    dead_outbound = sum(
        values.get(MessageStatus.dead.value, 0)
        for values in outbound_counts.values()
    )

    reason_codes: list[str] = []
    if stale_jobs:
        reason_codes.append("background_jobs_stale_processing")
    if stale_outbound:
        reason_codes.append("outbound_stale_processing")
    if oldest_job_age_ms is not None and oldest_job_age_ms > maximum_ready_age_ms:
        reason_codes.append("background_jobs_ready_age_slo_breached")
    if oldest_outbound_age_ms is not None and oldest_outbound_age_ms > maximum_ready_age_ms:
        reason_codes.append("outbound_ready_age_slo_breached")
    if dead_jobs:
        reason_codes.append("background_jobs_dead_present")
    if dead_outbound:
        reason_codes.append("outbound_dead_present")

    blocking = any(
        code
        in {
            "background_jobs_stale_processing",
            "outbound_stale_processing",
            "background_jobs_ready_age_slo_breached",
            "outbound_ready_age_slo_breached",
        }
        for code in reason_codes
    )
    status = "not_ready" if blocking else ("degraded" if reason_codes else "ready")
    return {
        "schema": "nexus.queue-business-health.v1",
        "status": status,
        "reason_codes": reason_codes,
        "thresholds": {
            "maximum_ready_age_seconds": maximum_ready_age_seconds,
            "job_lock_seconds": settings.job_lock_seconds,
            "outbox_lock_seconds": settings.outbox_lock_seconds,
        },
        "background_jobs": {
            "counts": dict(sorted(job_counts.items())),
            "oldest_pending_age_ms": oldest_job_age_ms,
            "stale_processing": stale_jobs,
            "dead": dead_jobs,
        },
        "outbound": {
            "counts": dict(sorted(outbound_counts.items())),
            "oldest_pending_age_ms": oldest_outbound_age_ms,
            "stale_processing": stale_outbound,
            "dead": dead_outbound,
        },
        "contains_payloads": False,
    }
