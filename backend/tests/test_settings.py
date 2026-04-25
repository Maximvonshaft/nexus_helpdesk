from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.settings import get_settings


def _load_settings(monkeypatch, *, app_env: str, allow_dev_auth: str):
    monkeypatch.setenv('APP_ENV', app_env)
    monkeypatch.setenv('ALLOW_DEV_AUTH', allow_dev_auth)
    monkeypatch.setenv('SECRET_KEY', 'unit-test-secret-value')
    monkeypatch.setenv('DATABASE_URL', 'postgresql+psycopg://test:test@localhost:5432/testdb')
    monkeypatch.setenv('ALLOWED_ORIGINS', 'https://example.test')
    get_settings.cache_clear()
    try:
        return get_settings()
    finally:
        get_settings.cache_clear()


def test_allow_dev_auth_environment_matrix(monkeypatch):
    disabled_envs = ['production', 'staging', 'preview', 'demo']
    for env_name in disabled_envs:
        settings = _load_settings(monkeypatch, app_env=env_name, allow_dev_auth='true')
        assert settings.allow_dev_auth is False, env_name

    enabled_envs = ['development', 'test', 'local']
    for env_name in enabled_envs:
        settings = _load_settings(monkeypatch, app_env=env_name, allow_dev_auth='true')
        assert settings.allow_dev_auth is True, env_name

    settings = _load_settings(monkeypatch, app_env='development', allow_dev_auth='false')
    assert settings.allow_dev_auth is False
