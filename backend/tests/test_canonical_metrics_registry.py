from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CONTROLLED_COMPOSE = REPO_ROOT / "deploy" / "docker-compose.controlled.yml"
OBSERVABILITY = REPO_ROOT / "backend" / "app" / "services" / "observability.py"
GUNICORN_CONFIG = REPO_ROOT / "backend" / "gunicorn.conf.py"
DOCKERFILE = REPO_ROOT / "Dockerfile"


def test_controlled_runtime_uses_one_shared_prometheus_registry() -> None:
    compose = CONTROLLED_COMPOSE.read_text(encoding="utf-8")

    assert "PROMETHEUS_MULTIPROC_DIR: /var/run/nexus-prometheus" in compose
    assert 'PROMETHEUS_MULTIPROC_DIR: ""' in compose
    assert "prometheus-multiproc:/var/run/nexus-prometheus" in compose
    assert "find /var/run/nexus-prometheus -maxdepth 1 -type f -name '*.db' -delete" in compose
    assert "--config /app/backend/gunicorn.conf.py" in compose
    assert compose.count("prometheus-multiproc:/var/run/nexus-prometheus") == 1
    assert compose.count("PROMETHEUS_MULTIPROC_DIR:") == 2
    assert "worker-metrics" not in compose
    assert "pushgateway" not in compose.lower()


def test_observability_owns_multiprocess_collection_and_live_gauge_modes() -> None:
    source = OBSERVABILITY.read_text(encoding="utf-8")

    assert '_PROMETHEUS_MULTIPROC_DIR = (os.getenv("PROMETHEUS_MULTIPROC_DIR") or "").strip()' in source
    assert "_PROM_REGISTRY = None if _PROMETHEUS_MULTIPROC_ENABLED" in source
    assert "registry = CollectorRegistry()" in source
    assert "multiprocess.MultiProcessCollector(registry)" in source
    assert "multiprocess_mode='livesum'" in source
    assert "multiprocess_mode='livemax'" in source
    assert "def mark_prometheus_process_dead" in source


def test_gunicorn_and_image_use_the_same_metrics_cleanup_authority() -> None:
    gunicorn = GUNICORN_CONFIG.read_text(encoding="utf-8")
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "from app.services.observability import mark_prometheus_process_dead" in gunicorn
    assert "def child_exit" in gunicorn
    assert "mark_prometheus_process_dead(worker.pid)" in gunicorn
    assert "/var/run/nexus-prometheus" in dockerfile
    assert "-c /app/backend/gunicorn.conf.py" in dockerfile


def test_prometheus_multiprocess_scrape_combines_independent_processes(tmp_path: Path) -> None:
    script = """
from app.services.observability import record_worker_poll
record_worker_poll(__import__('sys').argv[1])
"""
    env = os.environ.copy()
    env["PROMETHEUS_MULTIPROC_DIR"] = str(tmp_path)
    env["PYTHONPATH"] = str(REPO_ROOT / "backend")

    for worker_id in ("worker-a", "worker-b"):
        subprocess.run(
            [sys.executable, "-c", script, worker_id],
            check=True,
            cwd=REPO_ROOT / "backend",
            env=env,
            capture_output=True,
            text=True,
        )

    scrape = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.services.observability import render_prometheus_metrics; print(render_prometheus_metrics())",
        ],
        check=True,
        cwd=REPO_ROOT / "backend",
        env=env,
        capture_output=True,
        text=True,
    ).stdout

    assert 'nexusdesk_worker_runs_total{worker_id="worker-a"} 1.0' in scrape
    assert 'nexusdesk_worker_runs_total{worker_id="worker-b"} 1.0' in scrape
