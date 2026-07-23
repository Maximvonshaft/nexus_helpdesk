from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

from app.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy" / "docker-compose.controlled.yml"
PREFLIGHT = ROOT / "scripts" / "deploy" / "validate_controlled_server_preflight.py"
SETTINGS = ROOT / "backend" / "app" / "settings.py"
BACKGROUND_BOUNDARY = (
    ROOT / "backend" / "app" / "services" / "background_job_transaction_boundary.py"
)
WORKER_RUNNER = ROOT / "backend" / "scripts" / "run_worker.py"


def _compose_document() -> dict:
    document = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    assert isinstance(document.get("services"), dict)
    return document


def _services() -> dict[str, dict]:
    return _compose_document()["services"]


def _service(name: str) -> dict:
    service = _services()[name]
    assert isinstance(service, dict)
    return service


def _environment(name: str) -> dict[str, str]:
    environment = _service(name).get("environment") or {}
    assert isinstance(environment, dict)
    return environment


def _volumes(name: str) -> list[str]:
    volumes = _service(name).get("volumes") or []
    assert isinstance(volumes, list)
    return [str(value) for value in volumes]


def _load_preflight_module():
    spec = importlib.util.spec_from_file_location(
        "controlled_preflight_contract",
        PREFLIGHT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _production_worker_env(tmp_path: Path, *, role: str) -> dict[str, str]:
    return {
        "APP_ENV": "production",
        "NEXUS_PROCESS_ROLE": role,
        "DATABASE_URL": (
            "postgresql+psycopg://worker:bounded-password@db:5432/nexusdesk"
        ),
        "TENANT_RUNTIME_AUTHORITY_MODE": "enforce",
        "AUTO_INIT_DB": "false",
        "SEED_DEMO_DATA": "false",
        "ALLOW_DEV_AUTH": "false",
        "ALLOW_LEGACY_INTEGRATION_API_KEY": "false",
        "STORAGE_BACKEND": "local",
        "UPLOAD_ROOT": str(tmp_path / role),
        "EXTERNAL_CHANNEL_TRANSPORT": "disabled",
        "EXTERNAL_CHANNEL_DEPLOYMENT_MODE": "disabled",
        "EXTERNAL_CHANNEL_CLI_FALLBACK_ENABLED": "false",
        "EXTERNAL_CHANNEL_BRIDGE_ENABLED": "false",
        "EXTERNAL_CHANNEL_SYNC_ENABLED": "false",
        "EXTERNAL_CHANNEL_INBOUND_AUTO_SYNC_ENABLED": "false",
        "EXTERNAL_CHANNEL_EVENT_DRIVER_ENABLED": "false",
        "WEBCHAT_AI_ENABLED": "false",
        "WEBCHAT_AI_AUTO_REPLY_MODE": "off",
        "WEBCHAT_AI_RECONCILER_ENABLED": "false",
        "PROVIDER_RUNTIME_ENABLED": "false",
        "PRIVATE_AI_RUNTIME_ENABLED": "false",
        "KNOWLEDGE_EMBEDDINGS_ENABLED": "false",
        "WEBCHAT_WS_ENABLED": "false",
        "WEBCHAT_WS_PUBLIC_ENABLED": "false",
        "WEBCHAT_WS_ADMIN_ENABLED": "false",
        "WEBCHAT_WS_BROKER": "database",
        "WHATSAPP_DISPATCH_MODE": "disabled",
        "EMAIL_MAILBOX_SYNC_ENABLED": "false",
        "METRICS_ENABLED": "false",
    }


def test_controlled_runtime_has_global_least_privilege_defaults():
    document = _compose_document()
    for service_name, service in document["services"].items():
        assert service.get("read_only") is True, service_name
        assert service.get("cap_drop") == ["ALL"], service_name
        assert "no-new-privileges:true" in (service.get("security_opt") or []), service_name
        assert service.get("pids_limit") == 256, service_name
        assert any(
            str(entry).startswith("/tmp:rw,noexec,nosuid")
            for entry in (service.get("tmpfs") or [])
        ), service_name
        assert "env_file" not in service, service_name

    text = COMPOSE.read_text(encoding="utf-8")
    assert "NEXUS_RUNTIME_SECRETS_HOST_PATH" not in text
    assert "/run/secrets" not in text


def test_retired_disabled_capability_credentials_are_absent():
    text = COMPOSE.read_text(encoding="utf-8")
    for forbidden in (
        "AI_RUNTIME_TOKEN_HOST_PATH",
        "LIVE_VOICE_TOKEN_HOST_PATH",
        "PRIVATE_AI_RUNTIME_TOKEN_FILE",
        "LIVE_VOICE_UPSTREAM_TOKEN_FILE",
        "/run/nexus/ai_runtime_token",
        "/run/nexus/live_voice_token",
    ):
        assert forbidden not in text


def test_migration_receives_only_database_and_metrics_volume():
    environment = _environment("migrate-controlled")
    assert "DATABASE_URL_MIGRATION" in str(environment["DATABASE_URL"])
    assert "prometheus-multiproc:/var/run/nexus-prometheus" in _volumes(
        "migrate-controlled"
    )
    for forbidden in (
        "SECRET_KEY",
        "RUNTIME_CONTRACT_SIGNING_SECRET",
        "METRICS_TOKEN",
        "ALLOWED_ORIGINS",
        "WEBCHAT_ALLOWED_ORIGINS",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
        "TELEPHONY_CONTROL_SECRET",
    ):
        assert forbidden not in environment
    assert all("uploads" not in volume for volume in _volumes("migrate-controlled"))


def test_web_process_owns_only_http_secrets_and_storage_mounts():
    environment = _environment("app-controlled")
    for required in (
        "SECRET_KEY",
        "RUNTIME_CONTRACT_SIGNING_SECRET",
        "METRICS_TOKEN",
        "ALLOWED_ORIGINS",
        "WEBCHAT_ALLOWED_ORIGINS",
    ):
        assert required in environment
    assert "DATABASE_URL_APP" in str(environment["DATABASE_URL"])
    volumes = _volumes("app-controlled")
    assert any(volume.endswith(":/app/backend/uploads:rw") for volume in volumes)
    assert any(
        volume.endswith(":/var/backups/nexusdesk/uploads:ro")
        for volume in volumes
    )


def test_outbound_worker_has_read_only_attachments_and_no_http_secret():
    environment = _environment("worker-outbound-controlled")
    assert "DATABASE_URL_OUTBOUND" in str(environment["DATABASE_URL"])
    assert any(
        volume.endswith(":/app/backend/uploads:ro")
        for volume in _volumes("worker-outbound-controlled")
    )
    for forbidden in (
        "SECRET_KEY",
        "RUNTIME_CONTRACT_SIGNING_SECRET",
        "METRICS_TOKEN",
        "ALLOWED_ORIGINS",
        "WEBCHAT_ALLOWED_ORIGINS",
        "TELEPHONY_CONTROL_SECRET",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
    ):
        assert forbidden not in environment
    assert all(
        "/var/backups/nexusdesk/uploads" not in volume
        for volume in _volumes("worker-outbound-controlled")
    )


def test_background_worker_has_writable_uploads_and_only_required_telephony_control():
    environment = _environment("worker-background-controlled")
    assert "DATABASE_URL_BACKGROUND" in str(environment["DATABASE_URL"])
    assert "QUEUE_METRICS_SNAPSHOT_INTERVAL_SECONDS" in environment
    assert "TELEPHONY_CONTROL_SECRET" in environment
    assert "LIVEKIT_API_KEY" in environment
    assert "LIVEKIT_API_SECRET" in environment
    assert any(
        volume.endswith(":/app/backend/uploads:rw")
        for volume in _volumes("worker-background-controlled")
    )
    for forbidden in (
        "SECRET_KEY",
        "RUNTIME_CONTRACT_SIGNING_SECRET",
        "METRICS_TOKEN",
        "ALLOWED_ORIGINS",
        "WEBCHAT_ALLOWED_ORIGINS",
    ):
        assert forbidden not in environment
    assert all(
        "/var/backups/nexusdesk/uploads" not in volume
        for volume in _volumes("worker-background-controlled")
    )


@pytest.mark.parametrize(
    "service,database_key,runtime_signing_required",
    [
        ("worker-webchat-ai-controlled", "DATABASE_URL_WEBCHAT_AI", True),
        ("worker-handoff-snapshot-controlled", "DATABASE_URL_HANDOFF", False),
    ],
)
def test_isolated_workers_have_no_unrelated_secret_or_file_mount(
    service: str,
    database_key: str,
    runtime_signing_required: bool,
):
    environment = _environment(service)
    assert database_key in str(environment["DATABASE_URL"])
    assert "prometheus-multiproc:/var/run/nexus-prometheus" in _volumes(service)
    assert ("RUNTIME_CONTRACT_SIGNING_SECRET" in environment) is runtime_signing_required
    for forbidden in (
        "SECRET_KEY",
        "METRICS_TOKEN",
        "ALLOWED_ORIGINS",
        "WEBCHAT_ALLOWED_ORIGINS",
        "TELEPHONY_CONTROL_SECRET",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
    ):
        assert forbidden not in environment
    assert all("uploads" not in volume for volume in _volumes(service))


def test_livekit_agent_has_media_credentials_without_database_or_http_authority():
    service = _service("livekit-agent-controlled")
    environment = _environment("livekit-agent-controlled")
    assert "telephony" in (service.get("profiles") or [])
    for required in (
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
        "LIVEKIT_AGENT_SHARED_SECRET",
    ):
        assert required in environment
    for forbidden in (
        "DATABASE_URL",
        "SECRET_KEY",
        "RUNTIME_CONTRACT_SIGNING_SECRET",
        "TELEPHONY_CONTROL_SECRET",
        "METRICS_TOKEN",
        "ALLOWED_ORIGINS",
        "WEBCHAT_ALLOWED_ORIGINS",
    ):
        assert forbidden not in environment
    assert all("uploads" not in volume for volume in _volumes("livekit-agent-controlled"))


def test_preflight_executes_the_same_role_isolation_contract():
    module = _load_preflight_module()
    module._validate_compose(COMPOSE)
    values: dict[str, str] = {}
    for index, key in enumerate(module.DATABASE_ROLE_KEYS.values(), start=1):
        values[key] = (
            f"postgresql+psycopg://role_{index}:bounded-password-{index}"
            "@10.2.64.2:5432/nexusdesk"
        )
    roles = module._validate_database_roles(
        values,
        expected_database_host="10.2.64.2",
        expected_database_port=5432,
    )
    assert set(roles) == set(module.DATABASE_ROLE_KEYS)
    assert len({row["username"] for row in roles.values()}) == len(roles)
    rendered = json.dumps(roles, sort_keys=True)
    for index in range(1, len(roles) + 1):
        assert f"bounded-password-{index}" not in rendered
    source = PREFLIGHT.read_text(encoding="utf-8")
    assert "nexus.osr.controlled-server-preflight.v2" in source
    assert "generic_database_url_forbidden" in source
    assert "compose_shared_env_file_forbidden" in source
    assert "disabled_capability_credential_forbidden" in source
    assert '"database_passwords_included": False' in source


@pytest.mark.parametrize(
    "role",
    [
        "migration",
        "worker-outbound",
        "worker-background",
        "worker-handoff-snapshot",
    ],
)
def test_non_http_production_roles_start_without_web_secrets(
    monkeypatch,
    tmp_path,
    role: str,
):
    for key, value in _production_worker_env(tmp_path, role=role).items():
        monkeypatch.setenv(key, value)
    for key in (
        "SECRET_KEY",
        "METRICS_TOKEN",
        "ALLOWED_ORIGINS",
        "WEBCHAT_ALLOWED_ORIGINS",
        "KNOWLEDGE_EMBEDDING_API_KEY",
        "KNOWLEDGE_EMBEDDING_API_KEY_FILE",
        "RUNTIME_CONTRACT_SIGNING_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)
    settings = Settings()
    assert settings.process_role == role
    assert settings.is_http_process is False
    assert settings.jwt_secret_key is None
    assert settings.knowledge_embeddings_enabled is False


def test_ai_worker_cannot_enable_ai_without_real_knowledge_runtime(
    monkeypatch,
    tmp_path,
):
    for key, value in _production_worker_env(
        tmp_path,
        role="worker-webchat-ai",
    ).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("WEBCHAT_AI_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_AI_AUTO_REPLY_MODE", "runtime")
    monkeypatch.setenv("KNOWLEDGE_EMBEDDINGS_ENABLED", "false")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.delenv("KNOWLEDGE_EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("KNOWLEDGE_EMBEDDING_API_KEY_FILE", raising=False)
    with pytest.raises(RuntimeError, match="KNOWLEDGE_EMBEDDINGS_ENABLED=true"):
        Settings()


def test_background_worker_claims_only_its_owned_queue_types():
    source = BACKGROUND_BOUNDARY.read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        ast.get_source_segment(source, node) or ""
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "dispatch_pending_background_jobs"
    )
    for required in (
        "SPEEDAF_WORK_ORDER_CREATE_JOB",
        "SPEEDAF_ADDRESS_UPDATE_JOB",
        "SPEEDAF_VOICE_CALLBACK_JOB",
        "EMAIL_MAILBOX_SYNC_JOB",
    ):
        assert required in function
    for forbidden in (
        "AUTO_REPLY_JOB",
        "WEBCHAT_AI_REPLY_JOB",
        "WEBCHAT_HANDOFF_SNAPSHOT_JOB",
        "EXTERNAL_CHANNEL_SYNC_JOB",
    ):
        assert forbidden not in function


def test_worker_runner_keeps_processed_counts_separate_from_queue_depth():
    runner = WORKER_RUNNER.read_text(encoding="utf-8")
    assert 'if queue == "webchat-ai"' in runner
    assert 'if queue == "handoff-snapshot"' in runner
    assert "collect_queue_health" in runner
    assert "_record_queue_depth_snapshot_if_due" in runner
    assert 'record_queue_snapshot("outbound", "processed"' not in runner
    assert 'record_queue_snapshot("background_job", "processed"' not in runner
    assert 'record_queue_snapshot("webchat_ai_reply", "processed"' not in runner


def test_process_role_authority_is_explicit_in_settings():
    source = SETTINGS.read_text(encoding="utf-8")
    for marker in (
        "VALID_PROCESS_ROLES",
        "HTTP_PROCESS_ROLES",
        "AI_CAPABLE_PROCESS_ROLES",
        "def _validate_production",
        "if self.is_http_process",
        "ai_execution_requested",
        "NEXUS_PROCESS_ROLE is not supported",
    ):
        assert marker in source


def test_external_database_network_remains_reachable():
    network = _compose_document()["networks"]["controlled-net"]
    assert network.get("driver") == "bridge"
    assert network.get("internal") is not True
