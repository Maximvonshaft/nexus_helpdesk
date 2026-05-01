from types import SimpleNamespace

import pytest

from app.enums import UserRole
from app.services.permissions import (
    CAP_AI_CONFIG_MANAGE,
    CAP_CHANNEL_ACCOUNT_MANAGE,
    CAP_MARKET_MANAGE,
    CAP_RUNTIME_MANAGE,
    CAP_USER_MANAGE,
    ALL_CAPABILITIES,
    resolve_capabilities,
)


def test_manager_default_system_governance_capabilities_are_removed():
    user = SimpleNamespace(id=1, role=UserRole.manager)
    caps = resolve_capabilities(user)
    assert CAP_USER_MANAGE not in caps
    assert CAP_CHANNEL_ACCOUNT_MANAGE not in caps
    assert CAP_AI_CONFIG_MANAGE not in caps
    assert CAP_RUNTIME_MANAGE not in caps
    assert CAP_MARKET_MANAGE not in caps


def test_admin_still_has_all_capabilities():
    user = SimpleNamespace(id=1, role=UserRole.admin)
    assert resolve_capabilities(user) == set(ALL_CAPABILITIES)


def test_openclaw_cli_fallback_default_false(monkeypatch):
    from app.settings import get_settings

    get_settings.cache_clear()
    monkeypatch.delenv("OPENCLAW_CLI_FALLBACK_ENABLED", raising=False)
    monkeypatch.setenv("APP_ENV", "development")
    settings = get_settings()
    assert settings.openclaw_cli_fallback_enabled is False
    get_settings.cache_clear()


def test_production_rejects_openclaw_cli_fallback(monkeypatch):
    from app.settings import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@localhost:5432/helpdesk")
    monkeypatch.setenv("SECRET_KEY", "strong-production-secret-value-for-test-only")
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://console.example.com")
    monkeypatch.setenv("OPENCLAW_CLI_FALLBACK_ENABLED", "true")
    with pytest.raises(RuntimeError, match="OPENCLAW_CLI_FALLBACK_ENABLED"):
        get_settings()
    get_settings.cache_clear()
