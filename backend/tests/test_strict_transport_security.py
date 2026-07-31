from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import STRICT_TRANSPORT_SECURITY, app, settings


def test_production_responses_emit_hsts(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")

    response = TestClient(app).get("/healthz")

    assert response.status_code == 200
    assert response.headers["strict-transport-security"] == STRICT_TRANSPORT_SECURITY


def test_nonproduction_responses_do_not_emit_hsts(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "test")

    response = TestClient(app).get("/healthz")

    assert response.status_code == 200
    assert "strict-transport-security" not in response.headers
