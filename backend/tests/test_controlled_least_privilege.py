from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy" / "docker-compose.controlled.yml"


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


def test_external_database_network_remains_reachable():
    text = COMPOSE.read_text(encoding="utf-8")
    network = text.split("networks:", 1)[1].split("volumes:", 1)[0]
    assert "driver: bridge" in network
    assert "internal: true" not in network
