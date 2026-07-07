import os
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webcall_ai_audio_reference_resolver_tests.db")

import pytest

from app import models, operator_models, tool_models, voice_models, webchat_models  # noqa: F401,E402
from app.db import Base, SessionLocal, engine
from app.services.webcall_ai.audio_reference_resolver import resolve_audio_reference_for_session
from app.services.webcall_ai.config import get_webcall_ai_settings
from app.utils.time import utc_now
from app.voice_models import WebchatVoiceSession


@pytest.fixture(autouse=True)
def clean_db_and_env(monkeypatch):
    get_webcall_ai_settings.cache_clear()
    for key in [
        "APP_ENV",
        "WEBCALL_AI_AUDIO_REFERENCE_SOURCE",
        "WEBCALL_AI_AUDIO_REFERENCE_STATIC_URL",
        "WEBCALL_AI_AUDIO_REFERENCE_ALLOWLIST",
        "WEBCALL_AI_AUDIO_REFERENCE_STATIC_ENABLED",
    ]:
        monkeypatch.delenv(key, raising=False)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    get_webcall_ai_settings.cache_clear()


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _voice_session(db) -> WebchatVoiceSession:
    now = utc_now()
    session = WebchatVoiceSession(
        public_id=f"voice_{uuid4().hex}",
        conversation_id=1,
        ticket_id=1,
        provider="livekit",
        provider_room_name=f"room_{uuid4().hex}",
        status="ringing",
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def _enable_static_fixture(monkeypatch, url: str = "https://media.example.test/call.wav") -> None:
    monkeypatch.setenv("WEBCALL_AI_AUDIO_REFERENCE_SOURCE", "static_fixture")
    monkeypatch.setenv("WEBCALL_AI_AUDIO_REFERENCE_STATIC_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_AI_AUDIO_REFERENCE_STATIC_URL", url)
    get_webcall_ai_settings.cache_clear()


def test_default_source_disabled_returns_none(db):
    session = _voice_session(db)

    assert resolve_audio_reference_for_session(session, "worker-a") is None


def test_static_fixture_rejected_unless_explicitly_enabled(monkeypatch):
    monkeypatch.setenv("WEBCALL_AI_AUDIO_REFERENCE_SOURCE", "static_fixture")
    monkeypatch.setenv("WEBCALL_AI_AUDIO_REFERENCE_STATIC_URL", "https://media.example.test/call.wav")
    get_webcall_ai_settings.cache_clear()

    with pytest.raises(RuntimeError, match="WEBCALL_AI_AUDIO_REFERENCE_STATIC_ENABLED"):
        get_webcall_ai_settings()


@pytest.mark.parametrize(
    "url",
    [
        "http://media.example.test/call.wav",
        "file:///tmp/call.wav",
        "/tmp/call.wav",
        "C:\\tmp\\call.wav",
        "https://localhost/call.wav",
        "https://127.0.0.1/call.wav",
    ],
)
def test_static_fixture_rejects_non_https_local_and_file_references(monkeypatch, url):
    _enable_static_fixture(monkeypatch, url)

    with pytest.raises(RuntimeError, match="WEBCALL_AI_AUDIO_REFERENCE_STATIC_URL"):
        get_webcall_ai_settings()


def test_static_fixture_requires_https_url(monkeypatch):
    _enable_static_fixture(monkeypatch, "")

    with pytest.raises(RuntimeError, match="WEBCALL_AI_AUDIO_REFERENCE_STATIC_URL"):
        get_webcall_ai_settings()


def test_allowlist_exact_host_passes(db, monkeypatch):
    session = _voice_session(db)
    _enable_static_fixture(monkeypatch, "https://media.example.test/call.wav")
    monkeypatch.setenv("WEBCALL_AI_AUDIO_REFERENCE_ALLOWLIST", "media.example.test, other.example.test")
    get_webcall_ai_settings.cache_clear()

    assert resolve_audio_reference_for_session(session, "worker-a") == "https://media.example.test/call.wav"


def test_allowlist_wrong_host_fails(monkeypatch):
    _enable_static_fixture(monkeypatch, "https://blocked.example.test/call.wav")
    monkeypatch.setenv("WEBCALL_AI_AUDIO_REFERENCE_ALLOWLIST", "media.example.test")
    get_webcall_ai_settings.cache_clear()

    with pytest.raises(RuntimeError, match="allowlist"):
        get_webcall_ai_settings()


def test_production_rejects_static_fixture(monkeypatch):
    _enable_static_fixture(monkeypatch, "https://media.example.test/call.wav")
    monkeypatch.setenv("APP_ENV", "production")
    get_webcall_ai_settings.cache_clear()

    with pytest.raises(RuntimeError, match="static_fixture"):
        get_webcall_ai_settings()
