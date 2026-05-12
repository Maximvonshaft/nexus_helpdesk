from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VOICE_ENTRY = ROOT / "backend" / "app" / "static" / "webchat" / "voice-entry.js"
DEMO_HTML = ROOT / "backend" / "app" / "static" / "webchat" / "demo.html"
ADMIN_ROUTE = ROOT / "webapp" / "src" / "routes" / "webchat-voice.tsx"
WEBCALL_ROUTE = ROOT / "webapp" / "src" / "routes" / "webcall.tsx"
ROUTER = ROOT / "webapp" / "src" / "router.tsx"
PACKAGE_JSON = ROOT / "webapp" / "package.json"


def test_public_voice_entry_calls_voice_session_api_and_webcall_page():
    text = VOICE_ENTRY.read_text(encoding="utf-8")

    assert "/api/webchat/voice/runtime-config" in text
    assert "/api/webchat/init" in text
    assert "/voice/sessions" in text
    assert "X-Webchat-Visitor-Token" in text
    assert "window.open" in text
    assert "/webcall/" in text
    assert "/webchat/voice/" in text
    assert "participant_token" in text
    assert "livekit_url" in text
    assert "Popup blocked. Please allow popups" in text


def test_demo_page_loads_optional_voice_entry():
    text = DEMO_HTML.read_text(encoding="utf-8")

    assert "/webchat/widget.js" in text
    assert "/webchat/voice-entry.js" in text
    assert "does not request microphone access" in text


def test_admin_mock_console_route_is_registered():
    route_text = ADMIN_ROUTE.read_text(encoding="utf-8")
    router_text = ROUTER.read_text(encoding="utf-8")

    assert "path: '/webchat-voice'" in route_text
    assert "webchatVoiceApi.acceptSession" in route_text
    assert "webchatVoiceApi.endSession" in route_text
    assert "WebchatVoiceRoute" in router_text
    assert "@/routes/webchat-voice" in router_text


def test_visitor_webcall_route_is_registered_and_click_to_join_only():
    route_text = WEBCALL_ROUTE.read_text(encoding="utf-8")
    router_text = ROUTER.read_text(encoding="utf-8")
    package_text = PACKAGE_JSON.read_text(encoding="utf-8")

    assert "path: '/webcall/$voice_session_id'" in route_text
    assert "createLocalAudioTrack" in route_text
    assert "RoomEvent" in route_text
    assert "room.connect" in route_text
    assert "publishTrack" in route_text
    assert "history.replaceState" in route_text
    assert "Microphone permission will be requested only after you click Join" in route_text
    assert "no recording in this phase" in route_text.lower()
    assert "createLocalAudioTrack" not in route_text.split("function joinCall", 1)[0]
    assert "WebCallRoute" in router_text
    assert "@/routes/webcall" in router_text
    assert "livekit-client" in package_text
    forbidden_terms = ["sip", "pstn", "twilio", "vonage", "recording_enabled", "transcription"]
    lowered = route_text.lower()
    assert not any(term in lowered for term in forbidden_terms)
