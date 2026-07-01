#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

python3 - "$ROOT_DIR" <<'PY'
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlparse

root = Path(sys.argv[1])

ENV_TEMPLATES = [
    root / 'deploy/.env.prod.example',
    root / 'deploy/.env.prod.local-postgres.example',
    root / 'deploy/.env.prod.external-postgres.example',
]
COMPOSE_PATH = root / 'deploy/docker-compose.server.yml'

PLACEHOLDER_PREFIXES = (
    'replace-with-',
    'your-',
)

REQUIRED_VALUES = {
    'APP_ENV': 'production',
    'AUTO_INIT_DB': 'false',
    'SEED_DEMO_DATA': 'false',
    'OUTBOUND_PROVIDER': 'disabled',
    'ENABLE_OUTBOUND_DISPATCH': 'false',
}


def parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def database_host(database_url: str) -> str:
    parsed = urlparse(database_url)
    return parsed.hostname or ''


def compose_has_postgres_service(text: str) -> bool:
    return bool(re.search(r'^\s{2}postgres:\s*$', text, flags=re.MULTILINE))


def assert_no_real_secret(key: str, value: str) -> None:
    safe_non_secret_keys = {
        # Numeric/session-duration config; contains TOKEN in the key name but is not a credential.
        "ACCESS_TOKEN_EXPIRE_HOURS",
        # Server path to secret files; the value is a directory, not credential material.
        "NEXUSDESK_RUNTIME_SECRETS_DIR",
    }
    if key in safe_non_secret_keys:
        return

    # Boolean feature flags may contain words like TOKEN/API_KEY in the key name,
    # but values such as false/true are not credentials.
    if value.strip().lower() in {"true", "false", "0", "1", "yes", "no", "on", "off"}:
        return

    upper = key.upper()
    if not any(part in upper for part in ('SECRET', 'PASSWORD', 'TOKEN', 'KEY')):
        return
    if not value:
        return
    if upper.endswith('_FILE') and value.startswith('/run/'):
        return
    if value.startswith(PLACEHOLDER_PREFIXES) or value == 'auto':
        return
    if value.startswith(('https://', 'http://', 'wss://', 'sqlite', 'postgresql')):
        return
    raise AssertionError(f'{key} in env example looks like a real secret; use a placeholder instead')


def validate_env(env_path: Path) -> None:
    if not env_path.exists():
        raise AssertionError(f'Missing env template: {env_path}')
    env = parse_env(env_path)

    for key, expected in REQUIRED_VALUES.items():
        actual = env.get(key)
        if actual != expected:
            raise AssertionError(f'{env_path}: expected {key}={expected}, got {actual!r}')

    for key, value in env.items():
        assert_no_real_secret(key, value)

    host = database_host(env.get('DATABASE_URL', ''))
    if env_path.name.endswith('external-postgres.example') and host == 'postgres':
        raise AssertionError(f'{env_path} is external-postgres but still uses host postgres')


if not COMPOSE_PATH.exists():
    raise AssertionError(f'Missing compose template: {COMPOSE_PATH}')
compose_text = COMPOSE_PATH.read_text(encoding='utf-8')
if not compose_has_postgres_service(compose_text):
    raise AssertionError(f'{COMPOSE_PATH} must define postgres service')
if 'EXTERNAL_CHANNEL_TRANSPORT: disabled' not in compose_text:
    raise AssertionError(f'{COMPOSE_PATH} must keep legacy ExternalChannel transport disabled')

for env_path in ENV_TEMPLATES:
    validate_env(env_path)

print('deploy contract ok')
PY
