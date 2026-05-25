from __future__ import annotations

import pytest

from app.settings import get_settings, Settings


def test_email_runtime_defaults_fail_closed(monkeypatch):
    for name in ("OUTBOUND_EMAIL_ENABLED", "EMAIL_PROVIDER", "EMAIL_DELIVERY_EVENTS_ENABLED", "EMAIL_INBOUND_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.outbound_email_enabled is False
    assert settings.email_provider == "disabled"
    assert settings.email_delivery_events_enabled is False
    assert settings.email_inbound_enabled is False


def test_outbound_provider_ses_is_rejected(monkeypatch):
    monkeypatch.setenv("OUTBOUND_PROVIDER", "ses")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="OUTBOUND_PROVIDER=ses"):
        Settings()


def test_email_enabled_requires_email_provider(monkeypatch):
    monkeypatch.setenv("OUTBOUND_PROVIDER", "openclaw")
    monkeypatch.setenv("OUTBOUND_EMAIL_ENABLED", "true")
    monkeypatch.setenv("EMAIL_PROVIDER", "disabled")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="EMAIL_PROVIDER=ses"):
        Settings()
