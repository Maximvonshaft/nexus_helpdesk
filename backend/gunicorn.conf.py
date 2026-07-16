from __future__ import annotations

from app.services.observability import mark_prometheus_process_dead


def child_exit(server, worker) -> None:  # noqa: ANN001
    """Remove dead-worker live Gauge files from the canonical metrics registry."""
    mark_prometheus_process_dead(worker.pid)
