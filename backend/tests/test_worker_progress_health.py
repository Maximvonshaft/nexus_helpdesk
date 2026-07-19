from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_controlled_workers_use_supervised_entrypoint_and_durable_health() -> None:
    compose = (ROOT / "deploy/docker-compose.controlled.yml").read_text(encoding="utf-8")
    assert compose.count("run_worker_supervised.py") == 4
    assert "scripts/check_worker_progress.py" in compose
    assert "/proc/1/cmdline" not in compose
    assert "controlled-worker-ok" not in compose
    for queue in ("outbound", "background", "webchat-ai", "handoff-snapshot"):
        assert f"NEXUS_WORKER_QUEUE: {queue}" in compose


def test_worker_progress_is_durable_bounded_and_payload_free() -> None:
    source = (ROOT / "backend/app/services/worker_progress.py").read_text(encoding="utf-8")
    assert "ServiceHeartbeat" in source
    assert "nexus.worker-progress.v1" in source
    assert '"contains_payloads": False' in source
    assert "record_worker_cycle_started" in source
    assert "record_worker_cycle_succeeded" in source
    assert "record_worker_cycle_failed" in source


def test_healthcheck_requires_recent_success_or_bounded_running_cycle() -> None:
    source = (ROOT / "backend/scripts/check_worker_progress.py").read_text(encoding="utf-8")
    assert "success_fresh" in source
    assert "running_fresh" in source
    assert "WORKER_PROGRESS_MAX_SUCCESS_AGE_SECONDS" in source
    assert "WORKER_PROGRESS_MAX_CYCLE_AGE_SECONDS" in source
