from pathlib import Path
from uuid import uuid4

import os

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webcall_ai_audio_reference_static_tests.db")

import pytest

from app import models, operator_models, tool_models, voice_models, webchat_fast_models, webchat_models  # noqa: F401,E402
from app.db import Base, SessionLocal, engine
from app.services.webcall_ai.config import get_webcall_ai_settings
from app.services.webcall_ai.deepgram_stt_provider import DeepgramSTTProvider
from app.services.webcall_ai.media_schemas import WebCallSTTInput
from app.services.webcall_ai.mock_turn_executor import execute_mock_turn_for_claimed_session
from app.services.webcall_ai.worker import run_webcall_ai_worker_once
from app.utils.time import utc_now
from app.voice_models import WebchatVoiceAIAction, WebchatVoiceAITurn, WebchatVoiceSession


ROOT = Path(__file__).resolve().parents[2]
RESOLVER = ROOT / "backend" / "app" / "services" / "webcall_ai" / "audio_reference_resolver.py"
MIGRATIONS = ROOT / "backend" / "alembic" / "versions"


class RecordingSTTProvider:
    name = "recording"

    def __init__(self) -> None:
        self.inputs: list[WebCallSTTInput] = []

    def transcribe(self, input: WebCallSTTInput):
        from app.services.webcall_ai.media_schemas import WebCallSTTResult

        self.inputs.append(input)
        return WebCallSTTResult(
            text_redacted="I need parcel help.",
            language=input.locale or "en",
            confidence=99,
            is_final=True,
            provider=self.name,
        )


class FakeDeepgramTransport:
    def __init__(self) -> None:
        self.calls = []

    def post_json(self, *, url: str, headers: dict[str, str], payload: dict[str, str], timeout_ms: int) -> dict:
        self.calls.append({"url": url, "headers": headers, "payload": payload, "timeout_ms": timeout_ms})
        return {
            "results": {
                "channels": [
                    {
                        "alternatives": [
                            {
                                "transcript": "Please track parcel ABC.",
                                "confidence": 0.91,
                            }
                        ]
                    }
                ]
            }
        }


@pytest.fixture(autouse=True)
def clean_db_and_env(monkeypatch):
    get_webcall_ai_settings.cache_clear()
    monkeypatch.setenv("WEBCALL_AI_AGENT_ENABLED", "true")
    for key in [
        "APP_ENV",
        "WEBCALL_STT_PROVIDER",
        "WEBCALL_STT_DEEPGRAM_ENABLED",
        "WEBCALL_STT_TOKEN",
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


def _voice_session(db, *, ai_agent_status: str | None = None, worker_id: str | None = None) -> WebchatVoiceSession:
    now = utc_now()
    session = WebchatVoiceSession(
        public_id=f"voice_{uuid4().hex}",
        conversation_id=1,
        ticket_id=1,
        provider="livekit",
        provider_room_name=f"room_{uuid4().hex}",
        status="ringing",
        ai_agent_status=ai_agent_status,
        ai_agent_worker_id=worker_id,
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


def test_mock_worker_default_path_unchanged(db):
    _voice_session(db)

    result = run_webcall_ai_worker_once(db, "worker-a", limit=10, lease_seconds=30)

    assert result == {
        "claimed": 1,
        "released": 1,
        "failed": 0,
        "skipped": 0,
        "turns": 1,
        "stt_events": 1,
        "tts_events": 1,
    }


def test_worker_passes_resolved_audio_reference_into_stt_input(db, monkeypatch):
    _enable_static_fixture(monkeypatch)
    session = _voice_session(db, ai_agent_status="claimed", worker_id="worker-a")
    provider = RecordingSTTProvider()
    monkeypatch.setattr("app.services.webcall_ai.mock_turn_executor.get_stt_provider", lambda: provider)

    result = execute_mock_turn_for_claimed_session(db, session=session, worker_id="worker-a")

    assert result.stt_events == 1
    assert provider.inputs[0].audio_reference == "https://media.example.test/call.wav"


def test_deepgram_disabled_audio_reference_fails_safely_without_rows(db, monkeypatch):
    session = _voice_session(db)
    monkeypatch.setenv("WEBCALL_STT_PROVIDER", "deepgram")
    monkeypatch.setenv("WEBCALL_STT_DEEPGRAM_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_STT_TOKEN", "local-token")
    get_webcall_ai_settings.cache_clear()

    result = run_webcall_ai_worker_once(db, "worker-a", limit=10, lease_seconds=30)
    db.refresh(session)

    assert result["claimed"] == 1
    assert result["failed"] == 1
    assert result["turns"] == 0
    assert result["stt_events"] == 0
    assert result["tts_events"] == 0
    assert session.ai_agent_status == "failed"
    assert db.query(WebchatVoiceAITurn).count() == 0
    assert db.query(WebchatVoiceAIAction).count() == 0


def test_deepgram_static_fixture_with_fake_transport_creates_turn_and_action(db, monkeypatch):
    _voice_session(db)
    _enable_static_fixture(monkeypatch)
    monkeypatch.setenv("WEBCALL_STT_PROVIDER", "deepgram")
    monkeypatch.setenv("WEBCALL_STT_DEEPGRAM_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_STT_TOKEN", "local-token")
    get_webcall_ai_settings.cache_clear()
    settings = get_webcall_ai_settings()
    transport = FakeDeepgramTransport()
    provider = DeepgramSTTProvider(settings, transport=transport)
    monkeypatch.setattr("app.services.webcall_ai.mock_turn_executor.get_stt_provider", lambda: provider)

    result = run_webcall_ai_worker_once(db, "worker-a", limit=10, lease_seconds=30)

    assert result["claimed"] == 1
    assert result["released"] == 1
    assert result["failed"] == 0
    assert result["turns"] == 1
    assert result["stt_events"] == 1
    assert result["tts_events"] == 1
    assert transport.calls[0]["payload"] == {"url": "https://media.example.test/call.wav"}
    assert db.query(WebchatVoiceAITurn).count() == 1
    assert db.query(WebchatVoiceAIAction).count() == 1


def test_pr7_adds_no_migration_file():
    migration_names = [path.name.lower() for path in MIGRATIONS.glob("*.py")]

    assert not any("wcall_ai3" in name for name in migration_names)
    assert not any("audio_reference" in name for name in migration_names)
    assert not any("pr7" in name for name in migration_names)


def test_audio_reference_resolver_has_no_runtime_or_network_imports():
    source = RESOLVER.read_text(encoding="utf-8").lower()

    for forbidden in [
        "livekit",
        "requests",
        "httpx",
        "aiohttp",
        "websocket",
        "websockets",
        "speedaf",
        "external_channel",
        "openai",
        "codex",
        "provider_runtime",
        "urllib",
    ]:
        assert forbidden not in source
