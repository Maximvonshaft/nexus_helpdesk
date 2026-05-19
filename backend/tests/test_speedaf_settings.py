from __future__ import annotations

import pytest

from app.settings import Settings


def test_settings_allows_speedaf_api_tracking_fact_source(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("WEBCHAT_TRACKING_FACT_SOURCE", "speedaf_api")
    settings = Settings()

    assert settings.webchat_tracking_fact_source == "speedaf_api"


def test_settings_rejects_unknown_tracking_fact_source(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("WEBCHAT_TRACKING_FACT_SOURCE", "unknown_source")

    with pytest.raises(RuntimeError, match="WEBCHAT_TRACKING_FACT_SOURCE"):
        Settings()
