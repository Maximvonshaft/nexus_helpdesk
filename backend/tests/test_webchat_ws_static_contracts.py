from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.settings import Settings

ROOT = Path(__file__).resolve().parents[2]


def test_webchat_ws_is_feature_flagged_by_default():
    old = {key: os.environ.get(key) for key in ("WEBCHAT_WS_ENABLED", "WEBCHAT_WS_ADMIN_ENABLED", "WEBCHAT_WS_PUBLIC_ENABLED", "WEBCHAT_WS_BROKER")}
    try:
        for key in old:
            os.environ.pop(key, None)
        settings = Settings()
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    assert settings.webchat_ws_enabled is False
    assert settings.webchat_ws_admin_enabled is False
    assert settings.webchat_ws_public_enabled is False
    assert settings.webchat_ws_broker == "database"


def test_webchat_ws_rejects_memory_broker_in_production(monkeypatch):
    real_exists = Path.exists

    def fake_exists(path):
        if path.name == "index.html" and path.parent.name == "frontend_dist":
            return True
        return real_exists(path)

    monkeypatch.setattr(Path, "exists", fake_exists)
    env = {
        "APP_ENV": "production",
        "SECRET_KEY": "ci-value-for-webchat-ws",
        "DATABASE_URL": "postgresql+psycopg://helpdesk:helpdesk@db:5432/helpdesk",
        "ALLOWED_ORIGINS": "https://example.test",
        "WEBCHAT_ALLOWED_ORIGINS": "https://example.test",
        "WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT": "false",
        "WEBCHAT_WS_ENABLED": "true",
        "WEBCHAT_WS_BROKER": "memory",
        "AUTO_INIT_DB": "false",
        "SEED_DEMO_DATA": "false",
        "ALLOW_DEV_AUTH": "false",
        "ALLOW_LEGACY_INTEGRATION_API_KEY": "false",
        "OPENCLAW_CLI_FALLBACK_ENABLED": "false",
        "OPENCLAW_TRANSPORT": "mcp",
        "OPENCLAW_DEPLOYMENT_MODE": "remote_gateway",
        "STORAGE_BACKEND": "s3",
    }
    old = {key: os.environ.get(key) for key in env}
    try:
        os.environ.update(env)
        with pytest.raises(RuntimeError) as exc:
            Settings()
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    assert "WEBCHAT_WS_BROKER=memory" in str(exc.value)


def test_nginx_webchat_ws_upgrade_contract():
    text = (ROOT / "deploy" / "nginx" / "default.conf").read_text(encoding="utf-8")

    assert "map $http_upgrade $connection_upgrade" in text
    assert "location = /api/webchat/ws" in text
    assert "proxy_set_header Upgrade $http_upgrade;" in text
    assert "proxy_set_header Connection $connection_upgrade;" in text
    assert "proxy_buffering off;" in text
    assert "proxy_read_timeout 75s;" in text


def test_static_widget_uses_ws_without_url_token_transport():
    text = (ROOT / "backend" / "app" / "static" / "webchat" / "widget.js").read_text(encoding="utf-8")

    assert "new WebSocket(wsUrl())" in text
    assert "visitor_token:" in text
    assert "visitor_token=" not in text
    assert "data-websocket" in text
