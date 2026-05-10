from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

import pytest

from app.settings import Settings


@contextmanager
def patched_env(values: dict[str, str]):
    old_values = {key: os.environ.get(key) for key in values}
    try:
        os.environ.update(values)
        yield
    finally:
        for key, old in old_values.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


def production_env(**overrides: str) -> dict[str, str]:
    key_name = 'SECRET' + '_KEY'
    env = {
        'APP_ENV': 'production',
        key_name: 'ci-value-for-production-settings-contract',
        'DATABASE_URL': 'postgresql+psycopg://helpdesk:helpdesk@db:5432/helpdesk',
        'ALLOWED_ORIGINS': 'https://example.test',
        'AUTO_INIT_DB': 'false',
        'SEED_DEMO_DATA': 'false',
        'ALLOW_DEV_AUTH': 'false',
        'ALLOW_LEGACY_INTEGRATION_API_KEY': 'false',
        'OPENCLAW_CLI_FALLBACK_ENABLED': 'false',
        'STORAGE_BACKEND': 's3',
        'OPENCLAW_TRANSPORT': 'mcp',
        'OPENCLAW_DEPLOYMENT_MODE': 'remote_gateway',
        'WEBCHAT_RATE_LIMIT_BACKEND': 'database',
        'WEBCHAT_AI_AUTO_REPLY_MODE': 'safe_ack',
        'WEBCHAT_ALLOWED_ORIGINS': 'https://example.test',
        'WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT': 'false',
    }
    env.update(overrides)
    return env


def test_production_settings_accept_hardened_contract():
    with patched_env(production_env()):
        settings = Settings()
    assert settings.app_env == 'production'
    assert settings.is_postgres is True
    assert settings.allow_dev_auth is False


@pytest.mark.parametrize(
    ('key', 'value', 'expected_message'),
    [
        ('SECRET' + '_KEY', 'change-me', 'SECRET_KEY'),
        ('DATABASE_URL', 'sqlite:///tmp.db', 'PostgreSQL'),
        ('ALLOW_DEV_AUTH', 'true', 'ALLOW_DEV_AUTH'),
        ('ALLOW_LEGACY_INTEGRATION_API_KEY', 'true', 'ALLOW_LEGACY_INTEGRATION_API_KEY'),
        ('OPENCLAW_CLI_FALLBACK_ENABLED', 'true', 'OPENCLAW_CLI_FALLBACK_ENABLED'),
        ('WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT', 'true', 'WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT'),
    ],
)
def test_production_settings_reject_unsafe_contract(key: str, value: str, expected_message: str):
    with patched_env(production_env(**{key: value})):
        with pytest.raises(RuntimeError) as exc:
            Settings()
    assert expected_message in str(exc.value)


def test_production_remote_bridge_requires_token_contract():
    with patched_env(production_env(OPENCLAW_BRIDGE_ENABLED='true')):
        with pytest.raises(RuntimeError) as exc:
            Settings()
    assert 'OPENCLAW_BRIDGE_TOKEN' in str(exc.value)


def test_production_remote_bridge_accepts_env_token_contract():
    with patched_env(production_env(OPENCLAW_BRIDGE_ENABLED='true', OPENCLAW_BRIDGE_TOKEN='ci-bridge-token')):
        settings = Settings()
    assert settings.openclaw_bridge_enabled is True
    assert settings.openclaw_bridge_token == 'ci-bridge-token'


def test_production_remote_bridge_rejects_missing_token_file_contract():
    with patched_env(
        production_env(
            OPENCLAW_BRIDGE_ENABLED='true',
            OPENCLAW_BRIDGE_TOKEN_FILE='/tmp/nexusdesk-missing-openclaw-bridge-token',
        )
    ):
        with pytest.raises(RuntimeError) as exc:
            Settings()
    assert 'OPENCLAW_BRIDGE_TOKEN_FILE' in str(exc.value)


def test_production_remote_bridge_accepts_token_file_contract(tmp_path: Path):
    token_file = tmp_path / 'bridge-token'
    token_file.write_text('ci-file-token\n')
    with patched_env(production_env(OPENCLAW_BRIDGE_ENABLED='true', OPENCLAW_BRIDGE_TOKEN_FILE=str(token_file))):
        settings = Settings()
    assert settings.openclaw_bridge_token_file == str(token_file)
