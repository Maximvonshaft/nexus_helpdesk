from __future__ import annotations

import pytest

from app.settings import get_settings
from app.services.whatsapp_embedded_signup_settings import (
    reset_whatsapp_embedded_signup_settings_cache,
)
from app.services.whatsapp_media_settings import (
    reset_whatsapp_media_settings_cache,
)
from app.services.whatsapp_runtime_settings import (
    reset_whatsapp_runtime_settings_cache,
)


@pytest.fixture(autouse=True)
def isolate_runtime_settings(monkeypatch: pytest.MonkeyPatch):
    """Give every backend test a deterministic non-production runtime baseline.

    Security-contract tests may explicitly override these values within the test.
    Clearing every cached settings authority before and after the test prevents
    production/enforce fixtures from leaking into unrelated suites.
    """

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("TENANT_RUNTIME_AUTHORITY_MODE", "shadow")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    monkeypatch.setenv("WHATSAPP_EMBEDDED_SIGNUP_ENABLED", "false")
    monkeypatch.setenv("WHATSAPP_MEDIA_ENABLED", "false")
    monkeypatch.setenv("WHATSAPP_MEDIA_SCANNER", "disabled")
    _reset_settings_caches()
    yield
    _reset_settings_caches()


def _reset_settings_caches() -> None:
    get_settings.cache_clear()
    reset_whatsapp_runtime_settings_cache()
    reset_whatsapp_embedded_signup_settings_cache()
    reset_whatsapp_media_settings_cache()
