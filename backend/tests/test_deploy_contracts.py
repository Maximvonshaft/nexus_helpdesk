from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONTROLLED = ROOT / "deploy" / "docker-compose.controlled.yml"
LOCAL_DB = ROOT / "deploy" / "docker-compose.controlled-postgres.yml"
CONTROLLED_ENV = ROOT / "deploy" / ".env.controlled.example"
LOCAL_ENV = ROOT / "deploy" / ".env.controlled.local-postgres.example"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _compose(path: Path) -> dict:
    document = yaml.safe_load(_read(path))
    assert isinstance(document, dict)
    assert isinstance(document.get("services"), dict)
    return document


def _env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in _read(path).splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def test_retired_deployment_aliases_are_physically_absent():
    for relative in (
        "deploy/docker-compose.server.yml",
        "deploy/docker-compose.candidate.yml",
        "deploy/.env.prod.example",
        "deploy/.env.prod.local-postgres.example",
        "deploy/.env.prod.external-postgres.example",
        "deploy/.env.candidate.example",
    ):
        assert not (ROOT / relative).exists(), relative


def test_canonical_app_worker_topology_exists_in_one_file_only():
    controlled = _compose(CONTROLLED)["services"]
    local_db = _compose(LOCAL_DB)["services"]

    expected_runtime = {
        "app-controlled",
        "worker-outbound-controlled",
        "worker-background-controlled",
        "worker-webchat-ai-controlled",
        "worker-handoff-snapshot-controlled",
    }
    assert expected_runtime.issubset(controlled)
    assert expected_runtime.isdisjoint(local_db)
    assert "postgres-controlled" not in controlled
    assert "postgres-controlled" in local_db
    assert "migrate-controlled" in local_db
    assert (
        local_db["migrate-controlled"]["depends_on"]["postgres-controlled"][
            "condition"
        ]
        == "service_healthy"
    )


def test_external_and_local_controlled_envs_use_distinct_service_identities():
    expected_users = {
        "DATABASE_URL_MIGRATION": "nexus_migration",
        "DATABASE_URL_APP": "nexus_app",
        "DATABASE_URL_OUTBOUND": "nexus_outbound",
        "DATABASE_URL_BACKGROUND": "nexus_background",
        "DATABASE_URL_WEBCHAT_AI": "nexus_webchat_ai",
        "DATABASE_URL_HANDOFF": "nexus_handoff",
    }
    for path, expected_host in (
        (CONTROLLED_ENV, "10.2.64.2"),
        (LOCAL_ENV, "postgres-controlled"),
    ):
        text = _read(path)
        found_users: set[str] = set()
        for key, expected_user in expected_users.items():
            match = re.search(
                rf"(?m)^{key}=postgresql\+psycopg://([^:]+):[^@]+@([^:/]+):5432/nexusdesk$",
                text,
            )
            assert match, (path, key)
            assert match.group(1) == expected_user
            assert match.group(2) == expected_host
            found_users.add(match.group(1))
        assert len(found_users) == len(expected_users)
        assert not re.search(r"(?m)^DATABASE_URL=", text)


def test_local_postgres_overlay_bootstraps_only_database_authority():
    compose = _read(LOCAL_DB)
    bootstrap = _read(ROOT / "deploy" / "postgres" / "init-controlled-roles.sh")

    assert "pgvector/pgvector:0.8.5-pg16@sha256:" in compose
    assert "init-controlled-roles.sh" in compose
    assert "controlled-postgres-data" in compose
    assert "NEXUS_DB_MIGRATION_USER" in bootstrap
    assert "ALTER DEFAULT PRIVILEGES" in bootstrap
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES" in bootstrap
    assert "GRANT USAGE, SELECT ON SEQUENCES" in bootstrap
    assert "DROP DATABASE" not in bootstrap
    assert "DROP ROLE" not in bootstrap


def _secret_holders(services: dict[str, dict], key: str) -> set[str]:
    return {
        service_name
        for service_name, service in services.items()
        if key in (service.get("environment") or {})
    }


def test_controlled_profile_keeps_external_effects_disabled_and_secrets_role_scoped():
    document = _compose(CONTROLLED)
    services = document["services"]
    env = _env(CONTROLLED_ENV)

    expected_disabled = {
        "PROVIDER_RUNTIME_ENABLED": "false",
        "PROVIDER_RUNTIME_TRAFFIC_MODE": "control",
        "PROVIDER_RUNTIME_KILL_SWITCH": "true",
        "PROVIDER_RUNTIME_CANARY_PERCENT": "0",
        "ENABLE_OUTBOUND_DISPATCH": "false",
        "OUTBOUND_PROVIDER": "disabled",
        "WHATSAPP_NATIVE_ENABLED": "false",
        "WHATSAPP_DISPATCH_MODE": "disabled",
        "WEBCHAT_HUMAN_CALL_ENABLED": "false",
        "WEBCHAT_LIVE_AI_VOICE_ENABLED": "false",
    }
    for key, value in expected_disabled.items():
        assert env[key] == value
    assert "WEBCHAT_VOICE_ENABLED" not in env

    expected_secret_holders = {
        "SECRET_KEY": {"app-controlled"},
        "RUNTIME_CONTRACT_SIGNING_SECRET": {
            "app-controlled",
            "worker-webchat-ai-controlled",
        },
        "TELEPHONY_CONTROL_SECRET": {
            "app-controlled",
            "worker-background-controlled",
        },
        "LIVEKIT_API_KEY": {
            "app-controlled",
            "livekit-agent-controlled",
            "worker-background-controlled",
        },
        "LIVEKIT_API_SECRET": {
            "app-controlled",
            "livekit-agent-controlled",
            "worker-background-controlled",
        },
        "LIVEKIT_API_KEY_FILE": {
            "app-controlled",
            "livekit-agent-controlled",
            "worker-background-controlled",
        },
        "LIVEKIT_API_SECRET_FILE": {
            "app-controlled",
            "livekit-agent-controlled",
            "worker-background-controlled",
        },
        "LIVEKIT_AGENT_SHARED_SECRET": {
            "app-controlled",
            "livekit-agent-controlled",
        },
        "LIVEKIT_AGENT_SHARED_SECRET_FILE": {
            "app-controlled",
            "livekit-agent-controlled",
        },
        "METRICS_TOKEN": {"app-controlled"},
    }
    for secret_name, expected_holders in expected_secret_holders.items():
        assert _secret_holders(services, secret_name) == expected_holders, secret_name

    for service_name, service in services.items():
        assert "env_file" not in service, service_name
        command = service.get("command")
        if isinstance(command, list):
            assert not {"sh", "bash", "/bin/sh", "/bin/bash"}.intersection(command)
            if "--queue" in command:
                queue_index = command.index("--queue")
                assert command[queue_index + 1] != "all", service_name

    raw = _read(CONTROLLED).lower()
    for forbidden in (
        "/run/secrets",
        "ai_runtime_token",
        "live_voice_token",
    ):
        assert forbidden not in raw
