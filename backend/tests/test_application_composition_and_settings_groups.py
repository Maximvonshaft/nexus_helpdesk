from __future__ import annotations

import json
from pathlib import Path

from app.settings import Settings

ROOT = Path(__file__).resolve().parents[2]


def test_router_composition_is_explicit_and_main_is_bounded():
    main = (ROOT / "backend/app/main.py").read_text(encoding="utf-8")
    routes = (ROOT / "backend/app/bootstrap/routers.py").read_text(encoding="utf-8")
    assert "register_api_routers(app)" in main
    assert "app.include_router(" not in main
    assert "def register_api_routers" in routes
    assert "dependencies=[Depends(enforce_admin_password_request_policy)]" in routes


def test_effective_safe_config_is_typed_and_contains_no_secret_values(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'settings.db'}")
    monkeypatch.setenv("SECRET_KEY", "synthetic-test-secret-never-exported")
    monkeypatch.setenv("S3_ACCESS_KEY", "synthetic-access")
    monkeypatch.setenv("S3_SECRET_KEY", "synthetic-secret")
    settings = Settings()

    payload = settings.effective_safe_config()
    rendered = json.dumps(payload, sort_keys=True)
    assert payload["schema"] == "nexus.effective-safe-config.v1"
    assert payload["contains_secret_values"] is False
    assert "synthetic-test-secret-never-exported" not in rendered
    assert "synthetic-access" not in rendered
    assert "synthetic-secret" not in rendered
    assert set(payload["groups"]) == {
        "database", "authentication", "storage", "outbound", "provider", "webchat", "voice", "compatibility"
    }


def test_retired_runtime_requests_are_detectable(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'settings.db'}")
    monkeypatch.setenv("EXTERNAL_CHANNEL_BRIDGE_ENABLED", "false")
    settings = Settings()
    settings.external_channel_bridge_enabled = True
    assert settings.retired_runtime_requests() == ("EXTERNAL_CHANNEL_BRIDGE_ENABLED",)
