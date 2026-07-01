from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _env(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in _read(path).splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _has_postgres_service(compose_text: str) -> bool:
    return bool(re.search(r"^\s{2}postgres:\s*$", compose_text, flags=re.MULTILINE))


def _db_host(env_values: dict[str, str]) -> str:
    return urlparse(env_values["DATABASE_URL"]).hostname or ""


def test_server_compose_includes_local_postgres_service():
    compose = _read("deploy/docker-compose.server.yml")
    assert _has_postgres_service(compose)
    assert "EXTERNAL_CHANNEL_TRANSPORT: disabled" in compose
    assert "EXTERNAL_CHANNEL_DEPLOYMENT_MODE: disabled" in compose
    assert "/opt/nexus_helpdesk/deploy/runtime_secrets/ai_runtime_token:/run/nexus/ai_runtime_token:ro" in compose


def test_local_postgres_env_contract():
    env = _env("deploy/.env.prod.local-postgres.example")
    assert _db_host(env) == "postgres"
    assert env["APP_ENV"] == "production"
    assert env["AUTO_INIT_DB"] == "false"
    assert env["SEED_DEMO_DATA"] == "false"
    assert env["OUTBOUND_PROVIDER"] == "disabled"
    assert env["ENABLE_OUTBOUND_DISPATCH"] == "false"
    assert env["OUTBOUND_EMAIL_ENCRYPTION_KEY_FILE"] == "/run/nexus/outbound_email_encryption_key"


def test_external_postgres_env_contract():
    env = _env("deploy/.env.prod.external-postgres.example")
    assert _db_host(env) != "postgres"
    assert env["APP_ENV"] == "production"
    assert env["AUTO_INIT_DB"] == "false"
    assert env["SEED_DEMO_DATA"] == "false"
    assert env["OUTBOUND_PROVIDER"] == "disabled"
    assert env["ENABLE_OUTBOUND_DISPATCH"] == "false"
    assert env["OUTBOUND_EMAIL_ENCRYPTION_KEY_FILE"] == "/run/nexus/outbound_email_encryption_key"


def test_default_env_template_keeps_outbound_disabled():
    env = _env("deploy/.env.prod.example")
    assert env["OUTBOUND_PROVIDER"] == "disabled"
    assert env["ENABLE_OUTBOUND_DISPATCH"] == "false"
    assert env["OUTBOUND_EMAIL_ENCRYPTION_KEY_FILE"] == "/run/nexus/outbound_email_encryption_key"
    assert env["EXTERNAL_CHANNEL_CLI_FALLBACK_ENABLED"] == "false"


def test_private_ai_runtime_uses_app_readable_runtime_secret_mount():
    env = _env("deploy/.env.prod.example")
    server_compose = _read("deploy/docker-compose.server.yml")
    candidate_compose = _read("deploy/docker-compose.candidate.yml")
    expected_mount = "/opt/nexus_helpdesk/deploy/runtime_secrets/ai_runtime_token:/run/nexus/ai_runtime_token:ro"

    assert env["PRIVATE_AI_RUNTIME_TOKEN_FILE"] == "/run/nexus/ai_runtime_token"
    assert env["STT_API_KEY_FILE"] == "/run/nexus/ai_runtime_token"
    assert env["LLM_API_KEY_FILE"] == "/run/nexus/ai_runtime_token"
    assert env["TTS_API_KEY_FILE"] == "/run/nexus/ai_runtime_token"
    assert env["KNOWLEDGE_EMBEDDING_API_KEY_FILE"] == "/run/nexus/ai_runtime_token"
    assert env["PRIVATE_AI_RUNTIME_TIMEOUT_SECONDS"] == "20"
    assert env["PRIVATE_AI_RUNTIME_MAX_PROMPT_CHARS"] == "1200"
    assert env["PROVIDER_RUNTIME_TIMEOUT_MS"] == "30000"
    assert expected_mount in server_compose
    assert expected_mount in candidate_compose
