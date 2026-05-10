from __future__ import annotations

import os
import sys
from datetime import timedelta
from pathlib import Path

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/worker_metrics_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.models import BackgroundJob  # noqa: E402
from app.services.observability import (  # noqa: E402
    record_oldest_pending_job_age,
    record_outbound_latency,
    record_worker_job_metric,
)
from app.utils.time import utc_now  # noqa: E402


def _job_wait_ms(job: BackgroundJob) -> float | None:
    if not job.created_at:
        return None
    return max((utc_now() - job.created_at).total_seconds() * 1000.0, 0.0)


def test_job_wait_ms_uses_created_at_without_payload_body() -> None:
    job = BackgroundJob(
        queue_name="test",
        job_type="unit.test",
        payload_json='{"message":"body must not be logged"}',
        created_at=utc_now() - timedelta(seconds=3),
    )
    wait_ms = _job_wait_ms(job)
    assert wait_ms is not None
    assert wait_ms >= 0


def test_worker_and_outbound_metric_helpers_accept_low_cardinality_labels() -> None:
    record_worker_job_metric("unit.test", "success", duration_ms=10, wait_ms=5, retry_count=0)
    record_oldest_pending_job_age("unit.test", 100)
    record_outbound_latency("whatsapp", "openclaw_bridge", "success", queued_to_sent_ms=20, provider_dispatch_ms=10)
