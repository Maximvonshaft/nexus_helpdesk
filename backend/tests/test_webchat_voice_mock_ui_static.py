from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VOICE_ENTRY = ROOT / "backend" / "app" / "static" / "webchat" / "voice-entry.js"
DEMO_HTML = ROOT / "backend" / "app" / "static" / "webchat" / "demo.html"
ADMIN_ROUTE = ROOT / "webapp" / "src" / "routes" / "webchat-voice.tsx"
ROUTER = ROOT / "webapp" / "src" / "router.tsx"


def test_public_voice_entry_is_entry_only_not_webrtc_runtime():
    content = VOICE_ENTRY.read_text(encoding="utf-8")

    assert "/api/webchat/voice/runtime-config" in content
    assert "/voice/sessions" in content
    assert "window.open" in content
    assert "getUserMedia" not in content
    assert "LiveKit" not in content
    assert "livekit" not in content
    assert "RTCPeerConnection" not in content
    assert "MediaRecorder" not in content


def test_demo_page_loads_voice_entry_as_optional_extension():
    content = DEMO_HTML.read_text(encoding="utf-8")

    assert "/webchat/widget.js" in content
    assert "/webchat/voice-entry.js" in content
    assert "does not request microphone access" in content


def test_admin_mock_console_is_registered_and_provider_is_mock_only():
    route_content = ADMIN_ROUTE.read_text(encoding="utf-8")
    router_content = ROUTER.read_text(encoding="utf-8")

    assert "path: '/webchat-voice'" in route_content
    assert "Mock state machine only" in route_content
    assert "No LiveKit" in route_content
    assert "webchatVoiceApi.acceptSession" in route_content
    assert "webchatVoiceApi.endSession" in route_content
    assert "WebchatVoiceRoute" in router_content
    assert "@/routes/webchat-voice" in router_content
