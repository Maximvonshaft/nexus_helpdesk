from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.main import app


def test_legacy_public_voice_route_hands_off_to_webcall_without_token_leak(monkeypatch):
    monkeypatch.setenv("WEBCHAT_VOICE_ENABLED", "true")
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/webchat/voice/session-123")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "voice-redirect.js" in response.text
    assert "data-voice-session-id='session-123'" in response.text
    assert "/webcall/session-123" in response.text
    assert "token" not in response.text.lower()


def test_legacy_public_voice_redirect_script_preserves_browser_hash():
    with open("backend/app/static/webchat/voice-redirect.js", encoding="utf-8") as handle:
        source = handle.read()

    assert "window.location.hash" in source
    assert "window.location.replace('/webcall/' + encodeURIComponent(sessionId) + window.location.hash)" in source
