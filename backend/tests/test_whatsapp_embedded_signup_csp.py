from __future__ import annotations

from app.services.whatsapp_embedded_signup_csp import (
    augment_embedded_signup_csp,
    embedded_signup_csp_enabled,
)
from app.services.whatsapp_embedded_signup_settings import (
    reset_whatsapp_embedded_signup_settings_cache,
)


def _configure(monkeypatch, *, enabled: bool) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("WHATSAPP_EMBEDDED_SIGNUP_ENABLED", str(enabled).lower())
    if enabled:
        monkeypatch.setenv("WHATSAPP_META_APP_ID", "123456789")
        monkeypatch.setenv("WHATSAPP_META_APP_SECRET", "a" * 48)
        monkeypatch.setenv("WHATSAPP_META_CONFIGURATION_ID", "987654321")
        monkeypatch.setenv("WHATSAPP_META_GRAPH_API_VERSION", "v24.0")
        monkeypatch.setenv(
            "WHATSAPP_EMBEDDED_SIGNUP_ALLOWED_ORIGIN",
            "https://mcs.speedaf.com",
        )
    reset_whatsapp_embedded_signup_settings_cache()


def test_csp_adds_only_required_meta_sources_and_preserves_existing_guards() -> None:
    original = (
        "default-src 'self'; script-src 'self'; connect-src 'self'; "
        "object-src 'none'; frame-ancestors 'none'"
    )
    rendered = augment_embedded_signup_csp(original)

    assert "script-src 'self' https://connect.facebook.net" in rendered
    assert (
        "connect-src 'self' https://graph.facebook.com https://www.facebook.com"
        in rendered
    )
    assert "frame-src https://www.facebook.com https://web.facebook.com" in rendered
    assert "object-src 'none'" in rendered
    assert "frame-ancestors 'none'" in rendered
    assert rendered.count("script-src") == 1


def test_csp_is_enabled_only_for_channels_when_signup_is_configured(monkeypatch) -> None:
    _configure(monkeypatch, enabled=True)
    assert embedded_signup_csp_enabled("/channels") is True
    assert embedded_signup_csp_enabled("/channels/") is True
    assert embedded_signup_csp_enabled("/runtime") is False
    assert embedded_signup_csp_enabled("/api/admin/whatsapp/connections") is False


def test_csp_remains_closed_when_signup_is_disabled(monkeypatch) -> None:
    _configure(monkeypatch, enabled=False)
    assert embedded_signup_csp_enabled("/channels") is False
