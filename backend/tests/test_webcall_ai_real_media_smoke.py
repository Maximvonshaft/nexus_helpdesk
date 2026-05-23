import os

import pytest

from app.services.webcall_ai.config import get_webcall_ai_settings
from app.services.webcall_ai.real_media_smoke import run_webcall_ai_real_media_smoke


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    get_webcall_ai_settings.cache_clear()
    for key in ["APP_ENV", "WEBCALL_AI_PILOT_REAL_MEDIA_ENABLED"]:
        monkeypatch.delenv(key, raising=False)
    yield
    get_webcall_ai_settings.cache_clear()


def test_real_media_smoke_missing_publisher_fails_closed(monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_PILOT_REAL_MEDIA_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()

    result = run_webcall_ai_real_media_smoke()

    assert result.ok is False
    assert result.error_code == "livekit_real_media_smoke_unavailable"


def test_real_media_smoke_fake_injected_publisher_can_pass(monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_PILOT_REAL_MEDIA_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()

    class FakePublisher:
        def publish_silent_frame(self) -> bool:
            return True

    result = run_webcall_ai_real_media_smoke(publisher=FakePublisher())

    assert result.ok is True
    assert result.error_code is None
