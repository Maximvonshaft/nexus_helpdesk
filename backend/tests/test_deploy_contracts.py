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


def test_local_postgres_env_and_compose_contract():
    env = _env("deploy/.env.prod.local-postgres.example")
    compose = _read("deploy/docker-compose.server.local-postgres.yml")
    assert _db_host(env) == "postgres"
    assert _has_postgres_service(compose)
    assert env["APP_ENV"] == "production"
    assert env["AUTO_INIT_DB"] == "false"
    assert env["SEED_DEMO_DATA"] == "false"
    assert env["OUTBOUND_PROVIDER"] == "disabled"
    assert env["ENABLE_OUTBOUND_DISPATCH"] == "false"


def test_external_postgres_env_and_compose_contract():
    env = _env("deploy/.env.prod.external-postgres.example")
    compose = _read("deploy/docker-compose.server.external-postgres.yml")
    assert _db_host(env) != "postgres"
    assert not _has_postgres_service(compose)
    assert "expects DATABASE_URL to point to an external PostgreSQL instance" in compose
    assert env["APP_ENV"] == "production"
    assert env["AUTO_INIT_DB"] == "false"
    assert env["SEED_DEMO_DATA"] == "false"
    assert env["OUTBOUND_PROVIDER"] == "disabled"
    assert env["ENABLE_OUTBOUND_DISPATCH"] == "false"


def test_default_env_template_keeps_outbound_disabled():
    env = _env("deploy/.env.prod.example")
    assert env["OUTBOUND_PROVIDER"] == "disabled"
    assert env["ENABLE_OUTBOUND_DISPATCH"] == "false"
    assert env["OPENCLAW_CLI_FALLBACK_ENABLED"] == "false"
