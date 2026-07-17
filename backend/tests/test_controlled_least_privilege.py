from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy" / "docker-compose.controlled.yml"


def _service_block(text: str, name: str) -> str:
    marker = f"  {name}:\n"
    start = text.index(marker) + len(marker)
    remaining = text[start:]
    next_service = remaining.find("\n  ")
    if next_service == -1:
        return remaining
    return remaining[:next_service]


def test_controlled_runtime_has_global_least_privilege_defaults():
    text = COMPOSE.read_text(encoding="utf-8")
    prefix = text.split("services:", 1)[0]
    assert "read_only: true" in prefix
    assert "cap_drop:\n    - ALL" in prefix
    assert "no-new-privileges:true" in prefix
    assert "pids_limit: 256" in prefix
    assert "/tmp:rw,noexec,nosuid" in prefix


def test_migration_receives_no_business_secrets_or_uploads():
    block = _service_block(COMPOSE.read_text(encoding="utf-8"), "migrate-controlled")
    assert "prometheus-multiproc" in block
    for forbidden in (
        "NEXUS_RUNTIME_SECRETS_HOST_PATH",
        "AI_RUNTIME_TOKEN_HOST_PATH",
        "LIVE_VOICE_TOKEN_HOST_PATH",
        "NEXUS_UPLOADS_HOST_PATH",
        "NEXUS_UPLOAD_BACKUP_HOST_PATH",
    ):
        assert forbidden not in block


def test_application_receives_only_app_runtime_and_voice_material():
    block = _service_block(COMPOSE.read_text(encoding="utf-8"), "app-controlled")
    assert "NEXUS_RUNTIME_SECRETS_HOST_PATH" in block
    assert "LIVE_VOICE_TOKEN_HOST_PATH" in block
    assert "NEXUS_UPLOADS_HOST_PATH" in block and ":rw" in block
    assert "NEXUS_UPLOAD_BACKUP_HOST_PATH" in block and ":ro" in block
    assert "AI_RUNTIME_TOKEN_HOST_PATH" not in block


def test_outbound_worker_has_read_only_attachments_and_no_ai_or_voice_token():
    block = _service_block(COMPOSE.read_text(encoding="utf-8"), "worker-outbound-controlled")
    assert "NEXUS_RUNTIME_SECRETS_HOST_PATH" in block
    assert "NEXUS_UPLOADS_HOST_PATH" in block and ":ro" in block
    assert "AI_RUNTIME_TOKEN_HOST_PATH" not in block
    assert "LIVE_VOICE_TOKEN_HOST_PATH" not in block
    assert "NEXUS_UPLOAD_BACKUP_HOST_PATH" not in block


def test_background_worker_has_no_ai_voice_or_backup_material():
    block = _service_block(COMPOSE.read_text(encoding="utf-8"), "worker-background-controlled")
    assert "NEXUS_RUNTIME_SECRETS_HOST_PATH" in block
    assert "NEXUS_UPLOADS_HOST_PATH" in block and ":rw" in block
    assert "AI_RUNTIME_TOKEN_HOST_PATH" not in block
    assert "LIVE_VOICE_TOKEN_HOST_PATH" not in block
    assert "NEXUS_UPLOAD_BACKUP_HOST_PATH" not in block


def test_ai_worker_has_only_ai_token_and_metrics_mount():
    block = _service_block(COMPOSE.read_text(encoding="utf-8"), "worker-webchat-ai-controlled")
    assert "AI_RUNTIME_TOKEN_HOST_PATH" in block
    assert "prometheus-multiproc" in block
    for forbidden in (
        "NEXUS_RUNTIME_SECRETS_HOST_PATH",
        "LIVE_VOICE_TOKEN_HOST_PATH",
        "NEXUS_UPLOADS_HOST_PATH",
        "NEXUS_UPLOAD_BACKUP_HOST_PATH",
    ):
        assert forbidden not in block


def test_handoff_worker_has_no_business_secret_or_file_mount():
    block = _service_block(COMPOSE.read_text(encoding="utf-8"), "worker-handoff-snapshot-controlled")
    assert "prometheus-multiproc" in block
    for forbidden in (
        "NEXUS_RUNTIME_SECRETS_HOST_PATH",
        "AI_RUNTIME_TOKEN_HOST_PATH",
        "LIVE_VOICE_TOKEN_HOST_PATH",
        "NEXUS_UPLOADS_HOST_PATH",
        "NEXUS_UPLOAD_BACKUP_HOST_PATH",
    ):
        assert forbidden not in block


def test_external_database_network_remains_reachable():
    text = COMPOSE.read_text(encoding="utf-8")
    network = text.split("networks:", 1)[1].split("volumes:", 1)[0]
    assert "driver: bridge" in network
    assert "internal: true" not in network
