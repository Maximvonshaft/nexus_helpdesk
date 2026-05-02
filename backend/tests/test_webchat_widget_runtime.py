from __future__ import annotations

from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
WIDGET = BACKEND / "app/static/webchat/widget.js"


def _widget() -> str:
    return WIDGET.read_text(encoding="utf-8")


def test_widget_defaults_to_session_storage_with_opt_in_persistence():
    content = _widget()
    assert "data-persist-session" in content
    assert "window.sessionStorage" in content
    assert "window.localStorage" in content
    assert "persistSession?window.localStorage:window.sessionStorage" in content


def test_widget_sends_client_message_id():
    content = _widget()
    assert "client_message_id" in content
    assert "randomUUID" in content
    assert "wcmsg-" in content


def test_widget_uses_incremental_poll_and_tracks_last_seen_id():
    content = _widget()
    assert "lastSeenMessageId" in content
    assert "after_id" in content
    assert "limit=50" in content


def test_widget_recovers_from_invalid_visitor_token():
    content = _widget()
    assert "clearSession" in content
    assert "e.status===403" in content
    assert "return init()" in content
