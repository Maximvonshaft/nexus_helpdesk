from __future__ import annotations

from pathlib import Path


STATIC_ROOT = Path(__file__).resolve().parents[1] / "app" / "static" / "webchat"
WIDGET_JS = STATIC_ROOT / "widget.js"
DEMO_JS = STATIC_ROOT / "demo" / "js" / "app.js"


def _widget_source() -> str:
    return WIDGET_JS.read_text(encoding="utf-8")


def _demo_source() -> str:
    return DEMO_JS.read_text(encoding="utf-8")


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


def test_demo_fast_reply_uses_differentiated_error_handling():
    source = _demo_source()

    assert "network_timeout" in source
    assert "http_error" in source
    assert "origin_forbidden" in source
    assert "api_error_code" in source
    assert "empty_reply" in source
    assert "render_error" in source
    assert "userVisibleErrorMessage" in source
    assert "classifiedError" in source
    assert "reportDemoError('webchat_demo_api_error'" in source
    assert "reportDemoError('webchat_demo_render_error'" in source


def test_demo_fast_reply_debug_context_is_diagnostic_and_sanitized():
    source = _demo_source()
    diagnostic_source = "\n".join([
        source[source.index("function makeDebugContext"):source.index("function withDebug")],
        source[source.index("function withDebug"):source.index("function classifiedError")],
        source[source.index("function classifiedError"):source.index("function reportDemoError")],
        source[source.index("function reportDemoError"):source.index("function userVisibleErrorMessage")],
        source[source.index("function backendErrorCode"):source.index("function sendFastReply")],
    ])

    assert "session_id: sessionId" in source
    assert "client_message_id" in source
    assert "request_path: CONFIG.fastReplyPath" in source
    assert "http_status" in source
    assert "backend_error_code" in source
    assert "token" not in diagnostic_source.lower()
    assert "authorization" not in diagnostic_source.lower()
    assert "bearer" not in diagnostic_source.lower()


def test_demo_success_render_error_does_not_convert_success_to_connection_issue():
    source = _demo_source()

    render_block_start = source.index(".then(function (data)")
    render_block_end = source.index(".catch(function (error)", render_block_start)
    render_block = source[render_block_start:render_block_end]

    assert "appendMessage('bot', reply" in render_block
    assert "remember(body, reply)" in render_block
    assert "webchat_demo_render_error" in render_block
    assert "Connection issue. Please try again." not in render_block
