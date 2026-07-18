from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROLLED = ROOT / "deploy" / "docker-compose.controlled.yml"
LOCAL_DB = ROOT / "deploy" / "docker-compose.controlled-postgres.yml"
SERVER_ALIAS = ROOT / "deploy" / "docker-compose.server.yml"
CANDIDATE_ALIAS = ROOT / "deploy" / "docker-compose.candidate.yml"
CONTROLLED_ENV = ROOT / "deploy" / ".env.controlled.example"
LOCAL_ENV = ROOT / "deploy" / ".env.controlled.local-postgres.example"
PROD_TOMBSTONES = (
    ROOT / "deploy" / ".env.prod.example",
    ROOT / "deploy" / ".env.prod.local-postgres.example",
    ROOT / "deploy" / ".env.prod.external-postgres.example",
    ROOT / "deploy" / ".env.candidate.example",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in _read(path).splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def test_server_and_candidate_filenames_are_thin_canonical_aliases():
    server = _read(SERVER_ALIAS)
    candidate = _read(CANDIDATE_ALIAS)

    assert "services:" not in server
    assert "services:" not in candidate
    assert "./docker-compose.controlled.yml" in server
    assert "./docker-compose.controlled-postgres.yml" in server
    assert "./.env.controlled.local-postgres" in server
    assert "./docker-compose.controlled.yml" in candidate
    assert "./.env.controlled" in candidate
    for forbidden in (
        "app-candidate",
        "worker-outbound-candidate",
        "whatsapp-sidecar-candidate",
        "legacy-worker",
        "runtime-warmer",
        "/run/secrets",
        "ai_runtime_token",
        "live_voice_token",
    ):
        assert forbidden not in server
        assert forbidden not in candidate


def test_canonical_app_worker_topology_exists_in_one_file_only():
    controlled = _read(CONTROLLED)
    local_db = _read(LOCAL_DB)

    for service in (
        "app-controlled:",
        "worker-outbound-controlled:",
        "worker-background-controlled:",
        "worker-webchat-ai-controlled:",
        "worker-handoff-snapshot-controlled:",
    ):
        assert service in controlled
        assert service not in local_db
    assert "postgres-controlled:" not in controlled
    assert "postgres-controlled:" in local_db
    assert "migrate-controlled:" in local_db
    assert "condition: service_healthy" in local_db


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

    assert "postgres:16.14-alpine3.22@sha256:" in compose
    assert "init-controlled-roles.sh" in compose
    assert "controlled-postgres-data" in compose
    assert "NEXUS_DB_MIGRATION_USER" in bootstrap
    assert "ALTER DEFAULT PRIVILEGES" in bootstrap
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES" in bootstrap
    assert "GRANT USAGE, SELECT ON SEQUENCES" in bootstrap
    assert "DROP DATABASE" not in bootstrap
    assert "DROP ROLE" not in bootstrap


def test_retired_env_paths_are_bounded_tombstones():
    for path in PROD_TOMBSTONES:
        text = _read(path)
        env = _env(path)
        assert env["NEXUS_ENV_TEMPLATE_RETIRED"] == "true"
        assert "DATABASE_URL=" not in text
        assert "SECRET_KEY=" not in text
        assert "TOKEN_FILE=" not in text
        assert len(text.splitlines()) <= 20


def test_controlled_profile_keeps_external_effects_and_credentials_absent():
    compose = _read(CONTROLLED)
    env = _read(CONTROLLED_ENV)

    for marker in (
        "PROVIDER_RUNTIME_ENABLED=false",
        "PROVIDER_RUNTIME_TRAFFIC_MODE=control",
        "PROVIDER_RUNTIME_KILL_SWITCH=true",
        "PROVIDER_RUNTIME_CANARY_PERCENT=0",
        "ENABLE_OUTBOUND_DISPATCH=false",
        "OUTBOUND_PROVIDER=disabled",
        "WHATSAPP_NATIVE_ENABLED=false",
        "WHATSAPP_DISPATCH_MODE=disabled",
        "WEBCHAT_VOICE_ENABLED=false",
    ):
        assert marker in env
    for forbidden in (
        "env_file:",
        "/run/secrets",
        "ai_runtime_token",
        "live_voice_token",
        "RUNTIME_CONTRACT_SIGNING_SECRET",
        "--queue all",
    ):
        assert forbidden not in compose
