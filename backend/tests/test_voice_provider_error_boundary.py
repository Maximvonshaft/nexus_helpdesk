from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "backend/app/services/webchat_voice_service.py"


def test_voice_provider_exceptions_never_cross_the_public_api_boundary():
    source = SERVICE.read_text(encoding="utf-8")

    assert "detail=str(exc)" not in source
    assert "action.provider_reason = str(exc)" not in source
    assert 'detail="voice_provider_unavailable"' in source
    assert 'action.provider_reason = "provider_command_failed"' in source
    assert source.count('"error_type": type(exc).__name__') >= 2
