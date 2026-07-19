from __future__ import annotations

from pathlib import Path

import pytest

from app.main import app
from app.services.operator_queue import OperatorQueueError, create_operator_task
from app.settings import Settings

ROOT = Path(__file__).resolve().parents[2]


def test_external_channel_mutation_routes_are_absent() -> None:
    routes = {
        (method, route.path)
        for route in app.routes
        for method in getattr(route, "methods", set())
    }
    forbidden = {
        ("POST", "/api/admin/external_channel/link"),
        ("POST", "/api/admin/external_channel/tickets/{ticket_id}/sync"),
        ("POST", "/api/admin/external_channel/sync/enqueue"),
        ("POST", "/api/admin/external_channel/sync/enqueue-stale"),
        ("POST", "/api/admin/external_channel/events/consume-once"),
        ("POST", "/api/admin/external_channel/unresolved-events/{event_id}/replay"),
        ("POST", "/api/admin/external_channel/unresolved-events/{event_id}/drop"),
        ("POST", "/api/admin/operator-queue/{task_id}/replay"),
    }
    assert routes.isdisjoint(forbidden)


def test_external_channel_runtime_modules_are_absent() -> None:
    services = ROOT / "backend/app/services"
    assert not (services / "external_channel_bridge.py").exists()
    assert not (services / "external_channel_runtime_service.py").exists()
    assert not (services / "outbound_adapters/whatsapp.py").exists()
    assert not (services / "webchat_ai_decision_runtime/prompt_builder.py").exists()


def test_background_worker_has_no_external_channel_job_authority() -> None:
    jobs = (ROOT / "backend/app/services/background_jobs.py").read_text(encoding="utf-8")
    boundary = (
        ROOT / "backend/app/services/background_job_transaction_boundary.py"
    ).read_text(encoding="utf-8")
    for marker in (
        "EXTERNAL_CHANNEL_SYNC_JOB",
        "ATTACHMENT_PERSIST_JOB",
        "dispatch_pending_sync_jobs",
    ):
        assert marker not in jobs
        assert marker not in boundary


def test_application_runtime_contains_no_schema_ddl() -> None:
    offenders = []
    for candidate in (ROOT / "backend/app").rglob("*.py"):
        source = candidate.read_text(encoding="utf-8")
        if any(
            marker in source.upper()
            for marker in ("CREATE TABLE", "ALTER TABLE", "DROP TABLE")
        ):
            offenders.append(candidate.relative_to(ROOT).as_posix())
    assert offenders == []


def test_external_channel_task_creation_is_fail_closed_before_database_access() -> None:
    with pytest.raises(OperatorQueueError) as exc_info:
        create_operator_task(
            None,  # type: ignore[arg-type]
            source_type="external_channel",
            task_type="sync_failure",
        )
    assert exc_info.value.status_code == 410
    assert exc_info.value.code == "legacy_operator_task_creation_retired"


def test_retired_runtime_configuration_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("EXTERNAL_CHANNEL_SYNC_ENABLED", "true")
    with pytest.raises(RuntimeError, match="EXTERNAL_CHANNEL_SYNC_ENABLED has been retired"):
        Settings()


def test_deployment_surfaces_do_not_expose_retired_runtime() -> None:
    surfaces = (
        "backend/.env.example",
        "deploy/.env.controlled.example",
        "deploy/.env.controlled.local-postgres.example",
        "deploy/.env.rc-test.example",
        "deploy/docker-compose.controlled.yml",
        "deploy/docker-compose.operations-dispatch.yml",
        "deploy/nginx/nexusdesk.edge.conf.template",
        "deploy/nginx/nexusdesk.edge.env.example",
        "scripts/deploy/preflight.sh",
        "scripts/probe_nexus_runtime.sh",
        "backend/scripts/validate_production_readiness.py",
    )
    forbidden = (
        "EXTERNAL_CHANNEL_",
        "/api/admin/external_channel/",
        "NEXUSDESK_INTERNAL_AUTHORIZATION",
        "_nexusdesk_operator_auth",
    )
    offenders: list[str] = []
    for relative in surfaces:
        content = (ROOT / relative).read_text(encoding="utf-8")
        for marker in forbidden:
            if marker in content:
                offenders.append(f"{relative}:{marker}")
    assert offenders == []
