from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.main import DEFAULT_CSP, DEFAULT_PERMISSIONS_POLICY, app


def test_retired_public_voice_route_returns_normal_secure_404(monkeypatch):
    monkeypatch.setenv("WEBCHAT_HUMAN_CALL_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_VOICE_ALLOWED_PATH_PREFIXES", "/webcall")
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/webchat/voice/session-123")

    assert response.status_code == 404
    assert response.headers["permissions-policy"] == DEFAULT_PERMISSIONS_POLICY
    assert response.headers["content-security-policy"] == DEFAULT_CSP
    assert "voice-redirect.js" not in response.text
    assert "/webcall/session-123" not in response.text
    assert "token" not in response.text.lower()


def test_retired_public_voice_redirect_asset_is_absent():
    assert not Path("backend/app/static/webchat/voice-redirect.js").exists()
