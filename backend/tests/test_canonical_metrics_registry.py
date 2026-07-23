from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
CONTROLLED_COMPOSE = REPO_ROOT / "deploy" / "docker-compose.controlled.yml"
CONTROLLED_ENV_EXAMPLE = REPO_ROOT / "deploy" / ".env.controlled.example"
CONTROLLED_RUNBOOK = REPO_ROOT / "docs" / "releases" / "controlled-candidate-convergence-runbook.md"
APP_INIT = REPO_ROOT / "backend" / "app" / "__init__.py"
OBSERVABILITY = REPO_ROOT / "backend" / "app" / "services" / "observability.py"
GUNICORN_CONFIG = REPO_ROOT / "backend" / "gunicorn.conf.py"
WORKER_RUNNER = REPO_ROOT / "backend" / "scripts" / "run_worker.py"
DOCKERFILE = REPO_ROOT / "Dockerfile"


def _controlled_services() -> dict[str, dict]:
    document = yaml.safe_load(CONTROLLED_COMPOSE.read_text(encoding="utf-8"))
    services = document.get("services")
    assert isinstance(services, dict)
    return services


def _command_tokens(service: dict) -> list[str]:
    command = service.get("command")
    assert isinstance(command, list), "controlled services must use shell-less exec-vector commands"
    assert all(isinstance(token, str) and token for token in command)
    return command


def test_controlled_runtime_uses_one_shared_prometheus_registry() -> None:
    document = yaml.safe_load(CONTROLLED_COMPOSE.read_text(encoding="utf-8"))
    services = document["services"]
    env_example = CONTROLLED_ENV_EXAMPLE.read_text(encoding="utf-8")
    runbook = CONTROLLED_RUNBOOK.read_text(encoding="utf-8")

    assert "prometheus-multiproc" in document["volumes"]

    expected_mounts = {
        "migrate-controlled",
        "app-controlled",
        "worker-outbound-controlled",
        "worker-background-controlled",
        "worker-webchat-ai-controlled",
        "worker-handoff-snapshot-controlled",
    }
    mounted = {
        service_name
        for service_name, service in services.items()
        if "prometheus-multiproc:/var/run/nexus-prometheus" in (service.get("volumes") or [])
    }
    assert mounted == expected_mounts

    metrics_enabled = {
        service_name: str(service["environment"]["METRICS_ENABLED"]).lower()
        for service_name, service in services.items()
    }
    assert {name for name, value in metrics_enabled.items() if value == "true"} == {"app-controlled"}
    assert all(value in {"true", "false"} for value in metrics_enabled.values())

    token_holders = {
        service_name
        for service_name, service in services.items()
        if "METRICS_TOKEN" in service.get("environment", {})
    }
    assert token_holders == {"app-controlled"}
    assert services["app-controlled"]["environment"]["METRICS_TOKEN"] == "${METRICS_TOKEN:?set dedicated metrics token}"

    assert services["app-controlled"]["environment"]["PROMETHEUS_MULTIPROC_DIR"] == "/var/run/nexus-prometheus"
    assert services["migrate-controlled"]["environment"]["PROMETHEUS_MULTIPROC_DIR"] == ""
    assert services["livekit-agent-controlled"]["environment"]["PROMETHEUS_MULTIPROC_DIR"] == ""

    app_command = _command_tokens(services["app-controlled"])
    assert app_command[:6] == [
        "python",
        "-m",
        "gunicorn",
        "app.main:app",
        "--config",
        "/app/backend/gunicorn.conf.py",
    ]

    for service in services.values():
        command = service.get("command")
        if not isinstance(command, list):
            continue
        assert not {"sh", "bash", "/bin/sh", "/bin/bash"}.intersection(command)
        assert "find" not in command

    assert "METRICS_TOKEN=<dedicated-metrics-token-at-least-32-characters>" in env_example
    assert "METRICS_ENABLED=" not in env_example
    assert "unauthenticated `/metrics` returns 401" in runbook
    assert "worker-handoff-snapshot-controlled" in runbook
    assert "worker-metrics" not in services
    assert "pushgateway" not in CONTROLLED_COMPOSE.read_text(encoding="utf-8").lower()


def test_observability_owns_multiprocess_collection_and_container_safe_identity() -> None:
    app_init = APP_INIT.read_text(encoding="utf-8")
    source = OBSERVABILITY.read_text(encoding="utf-8")

    assert "socket.gethostname()" in app_init
    assert "def prometheus_process_identifier" in app_init
    assert "values.MultiProcessValue(process_identifier=prometheus_process_identifier)" in app_init
    assert "multiprocess.mark_process_dead = mark_process_dead" in app_init
    assert '_PROMETHEUS_MULTIPROC_DIR = (os.getenv("PROMETHEUS_MULTIPROC_DIR") or "").strip()' in source
    assert "_PROM_REGISTRY = None if _PROMETHEUS_MULTIPROC_ENABLED" in source
    assert "registry = CollectorRegistry()" in source
    assert "multiprocess.MultiProcessCollector(registry)" in source
    assert "multiprocess_mode='livesum'" in source
    assert "multiprocess_mode='livemax'" in source
    assert "def mark_prometheus_process_dead" in source


def test_app_and_worker_lifecycles_clean_live_metric_files() -> None:
    gunicorn = GUNICORN_CONFIG.read_text(encoding="utf-8")
    worker = WORKER_RUNNER.read_text(encoding="utf-8")
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    app_command = _command_tokens(_controlled_services()["app-controlled"])

    assert "from app.services.observability import mark_prometheus_process_dead" in gunicorn
    assert "def child_exit" in gunicorn
    assert "mark_prometheus_process_dead(worker.pid)" in gunicorn
    assert "signal.signal(signal.SIGTERM, request_shutdown)" in worker
    assert "signal.signal(signal.SIGINT, request_shutdown)" in worker
    assert "raise SystemExit(0)" in worker
    assert "/var/run/nexus-prometheus" in dockerfile
    assert app_command[app_command.index("--config") + 1] == "/app/backend/gunicorn.conf.py"
    assert 'CMD ["/usr/local/bin/python", "-m", "gunicorn", "app.main:app", "--config", "/app/backend/gunicorn.conf.py"' in dockerfile
    assert 'CMD ["sh"' not in dockerfile
    assert 'CMD ["bash"' not in dockerfile


def test_prometheus_multiprocess_scrape_combines_independent_container_namespaces(tmp_path: Path) -> None:
    script = """
from app.services.observability import record_worker_poll
record_worker_poll(__import__('sys').argv[1])
"""
    base_env = os.environ.copy()
    base_env["PROMETHEUS_MULTIPROC_DIR"] = str(tmp_path)
    base_env["PYTHONPATH"] = str(REPO_ROOT / "backend")

    for worker_id in ("worker-a", "worker-b"):
        env = base_env.copy()
        env["NEXUS_METRICS_PROCESS_NAMESPACE"] = worker_id
        subprocess.run(
            [sys.executable, "-c", script, worker_id],
            check=True,
            cwd=REPO_ROOT / "backend",
            env=env,
            capture_output=True,
            text=True,
        )

    mmap_names = {path.name for path in tmp_path.glob("*.db")}
    assert any(name.startswith("counter_worker-a-") for name in mmap_names)
    assert any(name.startswith("counter_worker-b-") for name in mmap_names)

    scrape_env = base_env.copy()
    scrape_env["NEXUS_METRICS_PROCESS_NAMESPACE"] = "scraper"
    scrape = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.services.observability import render_prometheus_metrics; print(render_prometheus_metrics())",
        ],
        check=True,
        cwd=REPO_ROOT / "backend",
        env=scrape_env,
        capture_output=True,
        text=True,
    ).stdout

    assert 'nexusdesk_worker_runs_total{worker_id="worker-a"} 1.0' in scrape
    assert 'nexusdesk_worker_runs_total{worker_id="worker-b"} 1.0' in scrape
