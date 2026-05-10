from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VOICE_ENTRY = ROOT / "backend" / "app" / "static" / "webchat" / "voice-entry.js"
DEMO_HTML = ROOT / "backend" / "app" / "static" / "webchat" / "demo.html"
ADMIN_ROUTE = ROOT / "webapp" / "src" / "routes" / "webchat-voice.tsx"
ROUTER = ROOT / "webapp" / "src" / "router.tsx"


def test_public_voice_entry_calls_mock_voice_session_api():
    text = VOICE_ENTRY.read_text(encoding="utf-8")

    assert "/api/webchat/voice/runtime-config" in text
    assert "/api/webchat/init" in text
    assert "/voice/sessions" in text
    assert "X-Webchat-Visitor-Token" in text
    assert "window.open" in text
    assert "/webchat/voice/" in text


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
