from __future__ import annotations

from datetime import timedelta

from app.models import BackgroundJob
from app.services.background_jobs import _job_wait_ms
from app.services.observability import record_worker_job_metric, record_outbound_latency, record_oldest_pending_job_age
from app.utils.time import utc_now


def test_job_wait_ms_uses_created_at_without_payload_body() -> None:
    job = BackgroundJob(
        queue_name='test',
        job_type='unit.test',
        payload_json='{"message":"body must not be logged"}',
        created_at=utc_now() - timedelta(seconds=3),
    )
    wait_ms = _job_wait_ms(job)
    assert wait_ms is not None
    assert wait_ms >= 0


def test_worker_and_outbound_metric_helpers_accept_low_cardinality_labels() -> None:
    record_worker_job_metric('unit.test', 'success', duration_ms=10, wait_ms=5, retry_count=0)
    record_oldest_pending_job_age('unit.test', 100)
    record_outbound_latency('whatsapp', 'openclaw_bridge', 'success', queued_to_sent_ms=20, provider_dispatch_ms=10)
