from __future__ import annotations

from pathlib import Path

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


def test_meta_sdk_timeout_resets_loader_for_operator_retry() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "webapp"
        / "src"
        / "lib"
        / "metaEmbeddedSignup.ts"
    ).read_text(encoding="utf-8")

    reset_block = source.split(
        "const resetMetaSdkLoad = (code: string) => {",
        1,
    )[1].split("\n    }", 1)[0]
    assert "window.clearTimeout(timeout)" in reset_block
    assert "document.getElementById(SDK_ID)?.remove()" in reset_block
    assert "sdkPromise = null" in reset_block
    assert "reject(new Error(code))" in reset_block

    timeout_block = source.split(
        "const timeout = window.setTimeout(() => {",
        1,
    )[1].split("}, 15_000)", 1)[0]
    assert "resetMetaSdkLoad('meta_sdk_load_timeout')" in timeout_block
