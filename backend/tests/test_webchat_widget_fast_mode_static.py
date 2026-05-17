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

    assert "/api/webchat/fast-reply/stream" in source
    assert "/api/webchat/fast-reply" in source
    assert "Accept': 'text/event-stream'" in source or 'Accept": "text/event-stream"' in source
    assert ".getReader" in source
    assert "EventSource" not in source
    assert "recent_context: buildApiRecentContext()" in source
    assert "recent_context: state.recentContext" not in source
    assert "sessionStorage.setItem(contextKey" in source


def test_widget_filters_api_recent_context_to_visitor_roles():
    source = _widget_source()

    assert "function buildApiRecentContext()" in source
    assert "role === 'visitor'" in source
    assert "role === 'customer'" in source
    assert "role === 'client'" in source
    assert "role === 'user'" in source
    assert "return { role: 'visitor'" in source


def test_widget_tracks_stream_bubble_states_and_partial_failure_copy():
    source = _widget_source()

    assert "setBubbleState(aiBubble, 'streaming')" in source
    assert "setBubbleState(aiBubble, 'complete')" in source
    assert "setBubbleState(aiBubble, 'failed_incomplete')" in source
    assert "setBubbleState(aiBubble, 'replayed_complete')" in source
    assert "This reply was interrupted. Please retry." in source
    assert "reply_delta" in source
    assert "replay" in source
    assert "final" in source


def test_widget_reuses_same_client_message_id_for_retry_and_non_stream_fallback():
    source = _widget_source()

    assert "sendFastMessage(retryBody, bubble, cmid)" in source
    assert "client_message_id: cmid" in source
    assert "fallbackToNonStream" in source


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
