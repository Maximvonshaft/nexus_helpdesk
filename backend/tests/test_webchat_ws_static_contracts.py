from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.routing import APIWebSocketRoute

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
        "EXTERNAL_CHANNEL_CLI_FALLBACK_ENABLED": "false",
        "EXTERNAL_CHANNEL_TRANSPORT": "disabled",
        "EXTERNAL_CHANNEL_DEPLOYMENT_MODE": "disabled",
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


def test_webchat_ws_runtime_dependency_and_route_registry_contract(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    requirements = (ROOT / "backend" / "requirements.txt").read_text(encoding="utf-8")

    assert "websockets==13.1" in requirements

    from app.main import app

    assert any(isinstance(route, APIWebSocketRoute) and route.path == "/api/webchat/ws" for route in app.routes)


def test_static_widget_uses_ws_without_url_token_transport():
    text = (ROOT / "backend" / "app" / "static" / "webchat" / "widget.js").read_text(encoding="utf-8")

    assert "new WebSocket(wsUrl())" in text
    assert "visitor_token:" in text
    assert "visitor_token=" not in text
    assert "data-websocket" in text


def test_static_widget_runtime_session_uses_public_ws_and_keeps_disable_fallback():
    text = (ROOT / "backend" / "app" / "static" / "webchat" / "widget.js").read_text(encoding="utf-8")

    assert "fast_ai" not in text
    assert "data-webchat-mode" not in text
    assert "rememberPublicSession(data)" in text
    assert "script.getAttribute('data-websocket') === 'false') return" in text
    assert "X-Webchat-WS-Fallback" in text


def test_static_widget_ai_turn_events_only_control_typing_state():
    text = (ROOT / "backend" / "app" / "static" / "webchat" / "widget.js").read_text(encoding="utf-8")

    assert "function syncAiTyping(status, pending, elapsedMs)" in text
    assert "normalized === 'bridge_calling'" in text
    assert "normalized === 'failed'" in text
    assert "normalized === 'timeout'" in text
    assert "ai_status_elapsed_ms" in text
    assert "data-ai-status" in text
    assert "AI processing" not in text
    assert "AI queued" not in text
    assert "AI retrying" not in text
    assert "indexOf('ai_turn.')" in text
    assert "Please provide your tracking number" not in text
    assert "so I can check" not in text


def test_static_widget_merges_server_echo_with_optimistic_message():
    text = (ROOT / "backend" / "app" / "static" / "webchat" / "widget.js").read_text(encoding="utf-8")

    assert "var clientKey = msg.client_message_id ? 'client:' + String(msg.client_message_id) : null;" in text
    assert "if (clientKey && state.rendered[clientKey])" in text
    assert "state.rendered[serverKey] = state.rendered[clientKey]" in text
    assert "var el = appendMessage(role, text, '', serverKey);" in text
    assert "if (clientKey) state.rendered[clientKey] = el;" in text


def test_webchat_static_assets_force_cache_revalidation():
    main = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
    demo = (ROOT / "backend" / "app" / "static" / "webchat" / "demo" / "index.html").read_text(encoding="utf-8")

    assert "request.url.path.startswith('/webchat/')" in main
    assert "request.url.path.startswith('/static/webchat/')" in main
    assert "no-cache, max-age=0, must-revalidate" in main
    assert "/webchat/widget.js?v=webchat-recovery-35ba9488" in demo
    assert "nexus-widget-consolidated-20260706" not in demo


def test_webchat_ws_observability_and_connection_limits_contract():
    settings_text = (ROOT / "backend" / "app" / "settings.py").read_text(encoding="utf-8")
    env_example = (ROOT / "backend" / ".env.example").read_text(encoding="utf-8")
    observability = (ROOT / "backend" / "app" / "services" / "observability.py").read_text(encoding="utf-8")
    ws_route = (ROOT / "backend" / "app" / "api" / "webchat_ws.py").read_text(encoding="utf-8")
    hub = (ROOT / "backend" / "app" / "services" / "webchat_realtime_hub.py").read_text(encoding="utf-8")

    assert "WEBCHAT_WS_MAX_CONNECTIONS" in settings_text
    assert "WEBCHAT_WS_MAX_CONNECTIONS_PER_USER" in settings_text
    assert "WEBCHAT_WS_MAX_CONNECTIONS=1000" in env_example
    assert "WEBCHAT_WS_MAX_CONNECTIONS_PER_USER=10" in env_example
    for metric in (
        "nexusdesk_webchat_websocket_connected_total",
        "nexusdesk_webchat_websocket_disconnected_total",
        "nexusdesk_webchat_websocket_auth_failed_total",
        "nexusdesk_webchat_websocket_event_sent_total",
        "nexusdesk_webchat_websocket_event_replay_total",
        "nexusdesk_webchat_websocket_fallback_polling_total",
        "nexusdesk_webchat_websocket_active_connections",
    ):
        assert metric in observability
    for event_name in (
        "websocket_connected",
        "websocket_disconnected",
        "websocket_auth_failed",
        "websocket_event_sent",
        "websocket_event_replay",
        "websocket_fallback_polling",
    ):
        assert event_name in ws_route
    assert "websocket_active_connections" in hub
    assert 'log_event(20, "websocket_connected", client_type=client_type)' in ws_route
    assert all("visitor_token" not in line and "access_token" not in line for line in ws_route.splitlines() if "log_event" in line)
