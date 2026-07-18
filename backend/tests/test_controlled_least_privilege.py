from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

from app.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy" / "docker-compose.controlled.yml"
ENV_EXAMPLE = ROOT / "deploy" / ".env.controlled.example"
PREFLIGHT = ROOT / "scripts" / "deploy" / "validate_controlled_server_preflight.py"
SETTINGS = ROOT / "backend" / "app" / "settings.py"
BACKGROUND_BOUNDARY = (
    ROOT / "backend" / "app" / "services" / "background_job_transaction_boundary.py"
)
WORKER_RUNNER = ROOT / "backend" / "scripts" / "run_worker.py"


def _service_blocks(text: str) -> dict[str, str]:
    services_text = text.split("\nservices:\n", 1)[1].split(
        "\nnetworks:\n",
        1,
    )[0]
    matches = list(re.finditer(r"(?m)^  ([a-z0-9-]+):\n", services_text))
    blocks: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = (
            matches[index + 1].start()
            if index + 1 < len(matches)
            else len(services_text)
        )
        blocks[match.group(1)] = services_text[match.end() : end]
    return blocks


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
    text = COMPOSE.read_text(encoding="utf-8")
    prefix = text.split("services:", 1)[0]
    assert "env_file:" not in text
    assert "read_only: true" in prefix
    assert "cap_drop:\n    - ALL" in prefix
    assert "no-new-privileges:true" in prefix
    assert "pids_limit: 256" in prefix
    assert "/tmp:rw,noexec,nosuid" in prefix
    assert "NEXUS_RUNTIME_SECRETS_HOST_PATH" not in text
    assert "/run/secrets" not in text


def test_disabled_capabilities_receive_no_credentials():
    text = COMPOSE.read_text(encoding="utf-8")
    for forbidden in (
        "AI_RUNTIME_TOKEN_HOST_PATH",
        "LIVE_VOICE_TOKEN_HOST_PATH",
        "PRIVATE_AI_RUNTIME_TOKEN_FILE",
        "LIVE_VOICE_UPSTREAM_TOKEN_FILE",
        "RUNTIME_CONTRACT_SIGNING_SECRET",
        "/run/nexus/ai_runtime_token",
        "/run/nexus/live_voice_token",
    ):
        assert forbidden not in text


def test_migration_receives_only_database_and_metrics_volume():
    block = _service_blocks(COMPOSE.read_text(encoding="utf-8"))[
        "migrate-controlled"
    ]
    assert "DATABASE_URL_MIGRATION" in block
    assert "prometheus-multiproc" in block
    for forbidden in (
        "SECRET_KEY",
        "METRICS_TOKEN",
        "ALLOWED_ORIGINS",
        "WEBCHAT_ALLOWED_ORIGINS",
        "NEXUS_UPLOADS_HOST_PATH",
        "NEXUS_UPLOAD_BACKUP_HOST_PATH",
    ):
        assert forbidden not in block


def test_web_process_owns_only_http_secrets_and_storage_mounts():
    block = _service_blocks(COMPOSE.read_text(encoding="utf-8"))[
        "app-controlled"
    ]
    assert "DATABASE_URL_APP" in block
    assert "SECRET_KEY" in block
    assert "METRICS_TOKEN" in block
    assert "ALLOWED_ORIGINS" in block
    assert "WEBCHAT_ALLOWED_ORIGINS" in block
    assert "NEXUS_UPLOADS_HOST_PATH" in block
    assert ":/app/backend/uploads:rw" in block
    assert "NEXUS_UPLOAD_BACKUP_HOST_PATH" in block
    assert ":/var/backups/nexusdesk/uploads:ro" in block


def test_outbound_worker_has_read_only_attachments_and_no_http_secret():
    block = _service_blocks(COMPOSE.read_text(encoding="utf-8"))[
        "worker-outbound-controlled"
    ]
    assert "DATABASE_URL_OUTBOUND" in block
    assert "NEXUS_UPLOADS_HOST_PATH" in block
    assert ":/app/backend/uploads:ro" in block
    for forbidden in (
        "SECRET_KEY",
        "METRICS_TOKEN",
        "ALLOWED_ORIGINS",
        "WEBCHAT_ALLOWED_ORIGINS",
        "NEXUS_UPLOAD_BACKUP_HOST_PATH",
    ):
        assert forbidden not in block


def test_background_worker_has_writable_uploads_only():
    block = _service_blocks(COMPOSE.read_text(encoding="utf-8"))[
        "worker-background-controlled"
    ]
    assert "DATABASE_URL_BACKGROUND" in block
    assert "NEXUS_UPLOADS_HOST_PATH" in block
    assert ":/app/backend/uploads:rw" in block
    assert "QUEUE_METRICS_SNAPSHOT_INTERVAL_SECONDS" in block
    for forbidden in (
        "SECRET_KEY",
        "METRICS_TOKEN",
        "ALLOWED_ORIGINS",
        "WEBCHAT_ALLOWED_ORIGINS",
        "NEXUS_UPLOAD_BACKUP_HOST_PATH",
    ):
        assert forbidden not in block


def test_ai_worker_has_no_provider_credential_while_ai_is_disabled():
    block = _service_blocks(COMPOSE.read_text(encoding="utf-8"))[
        "worker-webchat-ai-controlled"
    ]
    assert "DATABASE_URL_WEBCHAT_AI" in block
    assert "prometheus-multiproc" in block
    for forbidden in (
        "SECRET_KEY",
        "METRICS_TOKEN",
        "ALLOWED_ORIGINS",
        "WEBCHAT_ALLOWED_ORIGINS",
        "NEXUS_UPLOADS_HOST_PATH",
        "NEXUS_UPLOAD_BACKUP_HOST_PATH",
        "TOKEN",
    ):
        assert forbidden not in block


def test_handoff_worker_has_no_business_secret_or_file_mount():
    block = _service_blocks(COMPOSE.read_text(encoding="utf-8"))[
        "worker-handoff-snapshot-controlled"
    ]
    assert "DATABASE_URL_HANDOFF" in block
    assert "prometheus-multiproc" in block
    for forbidden in (
        "SECRET_KEY",
        "METRICS_TOKEN",
        "ALLOWED_ORIGINS",
        "WEBCHAT_ALLOWED_ORIGINS",
        "NEXUS_UPLOADS_HOST_PATH",
        "NEXUS_UPLOAD_BACKUP_HOST_PATH",
        "TOKEN",
    ):
        assert forbidden not in block


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

    with pytest.raises(
        RuntimeError,
        match="KNOWLEDGE_EMBEDDINGS_ENABLED=true",
    ):
        Settings()


def test_background_worker_claims_only_its_owned_queue_types():
    source = BACKGROUND_BOUNDARY.read_text(encoding="utf-8")
    function = source.split("def dispatch_pending_background_jobs", 1)[1].split(
        "def dispatch_pending_sync_jobs",
        1,
    )[0]
    for required in (
        "AUTO_REPLY_JOB",
        "ATTACHMENT_PERSIST_JOB",
        "SPEEDAF_WORK_ORDER_CREATE_JOB",
        "SPEEDAF_ADDRESS_UPDATE_JOB",
        "SPEEDAF_VOICE_CALLBACK_JOB",
        "EMAIL_MAILBOX_SYNC_JOB",
    ):
        assert required in function
    assert "WEBCHAT_AI_REPLY_JOB" not in function
    assert "WEBCHAT_HANDOFF_SNAPSHOT_JOB" not in function
    assert "EXTERNAL_CHANNEL_SYNC_JOB" not in function


def test_worker_runner_keeps_processed_counts_separate_from_queue_depth():
    runner = WORKER_RUNNER.read_text(encoding="utf-8")
    assert 'if queue == "webchat-ai"' in runner
    assert 'if queue in {"all", "handoff-snapshot"}' in runner
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
    text = COMPOSE.read_text(encoding="utf-8")
    network = text.rsplit("\nnetworks:\n", 1)[1].split("\nvolumes:\n", 1)[0]
    assert "driver: bridge" in network
    assert "internal: true" not in network
