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
        'WEBCHAT_WS_ENABLED': 'false',
        'WEBCHAT_WS_ADMIN_ENABLED': 'false',
        'WEBCHAT_WS_PUBLIC_ENABLED': 'false',
        'WEBCHAT_WS_BROKER': 'database',
        'KNOWLEDGE_EMBEDDINGS_ENABLED': 'true',
        'KNOWLEDGE_EMBEDDING_PROVIDER': 'openai_compatible',
        'KNOWLEDGE_EMBEDDING_MODEL': 'text-embedding-3-small',
        'KNOWLEDGE_EMBEDDING_API_KEY_FILE': '/run/secrets/knowledge_embedding_api_key',
    }
    env.update(overrides)
    return env


def test_production_settings_accept_hardened_contract(monkeypatch):
    real_exists = Path.exists

    def fake_exists(path):
        if path.name == 'index.html' and path.parent.name == 'frontend_dist':
            return True
        return real_exists(path)

    monkeypatch.setattr(Path, 'exists', fake_exists)

    with patched_env(production_env()):
        settings = Settings()
    assert settings.app_env == 'production'
    assert settings.is_postgres is True
    assert settings.allow_dev_auth is False
    assert settings.webchat_knowledge_reply_mode == 'ai_grounded'
    assert settings.webchat_ws_enabled is False
    assert settings.webchat_ws_broker == 'database'


@pytest.mark.parametrize(
    ('key', 'value', 'expected_message'),
    [
        ('SECRET' + '_KEY', 'change-me', 'SECRET_KEY'),
        ('DATABASE_URL', 'sqlite:///tmp.db', 'PostgreSQL'),
        ('ALLOW_DEV_AUTH', 'true', 'ALLOW_DEV_AUTH'),
        ('ALLOW_LEGACY_INTEGRATION_API_KEY', 'true', 'ALLOW_LEGACY_INTEGRATION_API_KEY'),
        ('OPENCLAW_CLI_FALLBACK_ENABLED', 'true', 'OPENCLAW_CLI_FALLBACK_ENABLED'),
        ('WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT', 'true', 'WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT'),
        ('WEBCHAT_KNOWLEDGE_REPLY_MODE', 'direct_answer', 'WEBCHAT_KNOWLEDGE_REPLY_MODE'),
        ('KNOWLEDGE_EMBEDDINGS_ENABLED', 'false', 'KNOWLEDGE_EMBEDDINGS_ENABLED'),
        ('KNOWLEDGE_EMBEDDING_PROVIDER', 'deterministic_hash', 'real embedding provider'),
        ('WEBCHAT_WS_BROKER', 'memory', 'WEBCHAT_WS_BROKER=memory'),
    ],
)
def test_production_settings_reject_unsafe_contract(key: str, value: str, expected_message: str):
    extra = {'WEBCHAT_WS_ENABLED': 'true'} if key == 'WEBCHAT_WS_BROKER' else {}
    with patched_env(production_env(**extra, **{key: value})):
        with pytest.raises(RuntimeError) as exc:
            Settings()
    assert expected_message in str(exc.value)
