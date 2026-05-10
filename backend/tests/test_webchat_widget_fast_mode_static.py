from __future__ import annotations

from pathlib import Path


WIDGET_JS = Path(__file__).resolve().parents[1] / "app" / "static" / "webchat" / "widget.js"


def _widget_source() -> str:
    return WIDGET_JS.read_text(encoding="utf-8")


def test_widget_defaults_to_fast_ai_mode():
    source = _widget_source()

    assert "data-webchat-mode" in source
    assert "'fast_ai'" in source
    assert "mode === 'legacy'" in source


def test_widget_calls_fast_reply_endpoint():
    source = _widget_source()

    assert "/api/webchat/fast-reply" in source
    assert "recent_context: state.recentContext" in source
    assert "sessionStorage.setItem(contextKey" in source


def test_widget_keeps_legacy_rollback_path():
    source = _widget_source()

    assert "/api/webchat/init" in source
    assert "/api/webchat/conversations/" in source
    assert "ensureLegacySession" in source
    assert "sendLegacyMessage" in source


def test_widget_does_not_expose_openclaw_gateway_details():
    source = _widget_source().lower()

    forbidden = [
        "openclaw_responses_url",
        "openclaw_responses_token",
        "openclaw_gateway_token",
        "openclaw-gateway",
        "/v1/responses",
        "bearer ",
    ]
    for needle in forbidden:
        assert needle not in source


def test_fast_error_state_does_not_append_template_agent_reply():
    source = _widget_source()

    assert "Speedy is reconnecting..." in source
    assert "Connection issue. Please try again." in source
    assert "A support specialist will review it shortly" not in source
    assert "We received your message and support will reply soon" not in source
