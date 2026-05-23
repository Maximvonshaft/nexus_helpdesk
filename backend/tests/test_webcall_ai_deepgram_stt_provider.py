import os

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webcall_ai_deepgram_stt_tests.db")

import pytest

from app.services.webcall_ai.config import get_webcall_ai_settings
from app.services.webcall_ai.deepgram_stt_provider import DeepgramSTTProvider
from app.services.webcall_ai.media_schemas import WebCallSTTInput


class FakeDeepgramTransport:
    def __init__(self, response=None, exc: Exception | None = None):
        self.response = response or {
            "results": {
                "channels": [
                    {
                        "alternatives": [
                            {
                                "transcript": "I need help with my parcel.",
                                "confidence": 0.923,
                            }
                        ]
                    }
                ]
            }
        }
        self.exc = exc
        self.calls = []

    def post_json(self, *, url: str, headers: dict[str, str], payload: dict[str, str], timeout_ms: int) -> dict:
        self.calls.append({"url": url, "headers": headers, "payload": payload, "timeout_ms": timeout_ms})
        if self.exc:
            raise self.exc
        return self.response


@pytest.fixture(autouse=True)
def clean_deepgram_env(monkeypatch):
    get_webcall_ai_settings.cache_clear()
    for key in [
        "APP_ENV",
        "WEBCALL_STT_PROVIDER",
        "WEBCALL_STT_DEEPGRAM_ENABLED",
        "WEBCALL_STT_TOKEN",
        "WEBCALL_STT_TOKEN_FILE",
        "WEBCALL_STT_DEEPGRAM_MODEL",
        "WEBCALL_STT_DEEPGRAM_SMART_FORMAT",
        "WEBCALL_STT_DEEPGRAM_ENDPOINT",
        "WEBCALL_STT_DEEPGRAM_REMOTE_URL_ALLOWLIST",
    ]:
        monkeypatch.delenv(key, raising=False)
    yield
    get_webcall_ai_settings.cache_clear()


def _deepgram_settings(monkeypatch, *, token: str = "local-token"):
    monkeypatch.setenv("WEBCALL_STT_PROVIDER", "deepgram")
    monkeypatch.setenv("WEBCALL_STT_DEEPGRAM_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_STT_TOKEN", token)
    get_webcall_ai_settings.cache_clear()
    return get_webcall_ai_settings()


def test_deepgram_provider_rejected_unless_enabled(monkeypatch):
    monkeypatch.setenv("WEBCALL_STT_PROVIDER", "deepgram")
    get_webcall_ai_settings.cache_clear()

    with pytest.raises(RuntimeError, match="WEBCALL_STT_DEEPGRAM_ENABLED"):
        get_webcall_ai_settings()


def test_production_deepgram_requires_token_file_and_rejects_inline_token(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("WEBCALL_STT_PROVIDER", "deepgram")
    monkeypatch.setenv("WEBCALL_STT_DEEPGRAM_ENABLED", "true")
    get_webcall_ai_settings.cache_clear()

    with pytest.raises(RuntimeError, match="WEBCALL_STT_TOKEN_FILE"):
        get_webcall_ai_settings()

    token_file = tmp_path / "deepgram-token"
    token_file.write_text("file-token", encoding="utf-8")
    monkeypatch.setenv("WEBCALL_STT_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("WEBCALL_STT_TOKEN", "inline-token")
    get_webcall_ai_settings.cache_clear()

    with pytest.raises(RuntimeError, match="WEBCALL_STT_TOKEN"):
        get_webcall_ai_settings()


def test_production_deepgram_rejects_non_https_endpoint(monkeypatch, tmp_path):
    token_file = tmp_path / "deepgram-token"
    token_file.write_text("file-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("WEBCALL_STT_PROVIDER", "deepgram")
    monkeypatch.setenv("WEBCALL_STT_DEEPGRAM_ENABLED", "true")
    monkeypatch.setenv("WEBCALL_STT_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("WEBCALL_STT_DEEPGRAM_ENDPOINT", "http://api.deepgram.test/v1/listen")
    get_webcall_ai_settings.cache_clear()

    with pytest.raises(RuntimeError, match="WEBCALL_STT_DEEPGRAM_ENDPOINT"):
        get_webcall_ai_settings()


def test_deepgram_provider_uses_fake_transport_request_contract(monkeypatch):
    settings = _deepgram_settings(monkeypatch)
    transport = FakeDeepgramTransport()
    provider = DeepgramSTTProvider(settings, transport=transport)

    result = provider.transcribe(
        WebCallSTTInput(
            voice_session_id=1,
            worker_id="worker-a",
            locale="en",
            audio_reference="https://media.example.test/call.wav",
        )
    )

    assert result.text_redacted == "I need help with my parcel."
    assert result.confidence == 92
    assert result.is_final is True
    assert result.status == "ok"
    assert result.provider == "deepgram"
    assert transport.calls == [
        {
            "url": "https://api.deepgram.com/v1/listen?model=nova-3&smart_format=true",
            "headers": {
                "Authorization": "Token local-token",
                "Content-Type": "application/json",
            },
            "payload": {"url": "https://media.example.test/call.wav"},
            "timeout_ms": 5000,
        }
    ]


def test_deepgram_rejects_non_https_audio_reference(monkeypatch):
    provider = DeepgramSTTProvider(_deepgram_settings(monkeypatch), transport=FakeDeepgramTransport())

    result = provider.transcribe(
        WebCallSTTInput(voice_session_id=1, worker_id="worker-a", audio_reference="http://media.example.test/call.wav")
    )

    assert result.status == "unavailable"
    assert result.error_code == "deepgram_audio_reference_must_be_https"


def test_deepgram_allowlist_blocks_unapproved_host(monkeypatch):
    monkeypatch.setenv("WEBCALL_STT_DEEPGRAM_REMOTE_URL_ALLOWLIST", "approved.example.test")
    settings = _deepgram_settings(monkeypatch)
    transport = FakeDeepgramTransport()
    provider = DeepgramSTTProvider(settings, transport=transport)

    result = provider.transcribe(
        WebCallSTTInput(
            voice_session_id=1,
            worker_id="worker-a",
            audio_reference="https://blocked.example.test/call.wav",
        )
    )

    assert result.status == "unavailable"
    assert result.error_code == "deepgram_audio_reference_host_not_allowed"
    assert transport.calls == []


def test_deepgram_missing_transcript_returns_safe_unavailable_result(monkeypatch):
    provider = DeepgramSTTProvider(
        _deepgram_settings(monkeypatch),
        transport=FakeDeepgramTransport(response={"results": {"channels": [{"alternatives": [{}]}]}}),
    )

    result = provider.transcribe(
        WebCallSTTInput(
            voice_session_id=1,
            worker_id="worker-a",
            audio_reference="https://media.example.test/call.wav",
        )
    )

    assert result.status == "unavailable"
    assert result.is_final is False
    assert result.text_redacted is None
    assert result.error_code == "deepgram_missing_transcript"


def test_deepgram_transport_error_returns_safe_unavailable_result(monkeypatch):
    provider = DeepgramSTTProvider(
        _deepgram_settings(monkeypatch, token="secret-token"),
        transport=FakeDeepgramTransport(exc=RuntimeError("secret-token raw provider payload")),
    )

    result = provider.transcribe(
        WebCallSTTInput(
            voice_session_id=1,
            worker_id="worker-a",
            audio_reference="https://media.example.test/call.wav",
        )
    )

    assert result.status == "unavailable"
    assert result.error_code == "deepgram_transport_error"
    assert "secret-token" not in result.error_code
