from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "backend/app/api/webchat_voice.py"
SESSION = ROOT / "backend/app/services/voice_session_service.py"
COMMANDS = ROOT / "backend/app/services/voice_command_dispatcher.py"
PROVIDER = ROOT / "backend/app/services/livekit_voice_provider.py"


def test_provider_exceptions_never_cross_customer_or_operator_api_boundaries():
    api = API.read_text(encoding="utf-8")
    session = SESSION.read_text(encoding="utf-8")
    commands = COMMANDS.read_text(encoding="utf-8")
    provider = PROVIDER.read_text(encoding="utf-8")
    combined = "\n".join((api, session, commands, provider))

    assert "detail=str(exc)" not in combined
    assert "provider_reason = str(exc)" not in combined
    assert "VoiceProviderError" in api
    assert "voice_room_cleanup_deferred" in api
    assert "serialize_voice_session" in api
    assert 'provider_reason="provider_command_failed"' in commands
    assert 'detail="voice provider command is temporarily unavailable"' in api
    assert combined.count('"error_type": type(exc).__name__') >= 2
