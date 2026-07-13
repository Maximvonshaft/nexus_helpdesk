from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VOICE_ENTRY = ROOT / "backend" / "app" / "static" / "webchat" / "voice-entry.js"
DEMO_HTML = ROOT / "backend" / "app" / "static" / "webchat" / "demo.html"
DEMO_INDEX = ROOT / "backend" / "app" / "static" / "webchat" / "demo" / "index.html"
WIDGET_JS = ROOT / "backend" / "app" / "static" / "webchat" / "widget.js"
CANDIDATE_SMOKE = ROOT / "scripts" / "smoke" / "production_candidate_smoke.sh"
ADMIN_ROUTE = ROOT / "webapp" / "src" / "routes" / "webchat-voice.tsx"
OPERATOR_ROUTE = ROOT / "webapp" / "src" / "routes" / "webcall-operator.tsx"
AGENT_PANEL = ROOT / "webapp" / "src" / "components" / "webcall" / "AgentWebCallPanel.tsx"
WEBCALL_ROUTE = ROOT / "webapp" / "src" / "routes" / "webcall.tsx"
ROUTER = ROOT / "webapp" / "src" / "router.tsx"
PACKAGE_JSON = ROOT / "webapp" / "package.json"


def test_public_voice_entry_delegates_to_widget_without_opening_existing_chat():
    text = VOICE_ENTRY.read_text(encoding="utf-8")
    widget = WIDGET_JS.read_text(encoding="utf-8")

    assert "/webchat/widget.js" in text
    assert "if (window.__NEXUSDESK_WEBCHAT_LOADED__) return;" in text
    assert "window.NexusDeskWebChat.open()" not in text
    assert "data-live-voice-mode" in text
    assert "data-live-voice-ws-path" in text
    assert "/webchat/live/ws" in widget
    assert "voiceStartBtn.addEventListener('click', startLiveVoice)" in widget
    assert "getUserMedia" in widget


def test_showcase_loads_consolidated_widget_with_feature_gated_webcall_entry():
    redirect_text = DEMO_HTML.read_text(encoding="utf-8")
    index_text = DEMO_INDEX.read_text(encoding="utf-8")

    assert "url=/webchat/demo/" in redirect_text
    assert "/webchat/widget.js" in index_text
    assert "js/app.js" not in index_text
    assert "/webchat/voice-entry.js" not in index_text
    assert "data-tenant=\"default\"" in index_text
    assert "data-channel=\"website\"" in index_text
    assert "data-title=\"Speedaf Support\"" in index_text
    assert "data-auto-open=\"true\"" in index_text
    assert "data-live-voice-mode=\"edge-card\"" in index_text
    assert "data-live-voice-ws-path=\"/webchat/live/ws\"" in index_text
    assert "data-live-voice-label=\"VOIP Call\"" in index_text


def test_showcase_support_chat_is_owned_by_widget_only():
    index_text = DEMO_INDEX.read_text(encoding="utf-8")
    widget_text = WIDGET_JS.read_text(encoding="utf-8")

    assert 'id="floatingChat"' not in index_text
    assert 'id="chatPanel"' not in index_text
    assert 'class="chat-panel is-closed"' not in index_text
    assert "data-webchat-form" in index_text
    assert "data-open-chat" in index_text
    assert "var autoOpen = script.getAttribute('data-auto-open') === 'true';" in widget_text
    assert "window.NexusDeskWebChat" in widget_text
    assert "bindPageTriggers()" in widget_text


def test_public_voice_entry_fails_closed_without_runtime_secrets():
    text = VOICE_ENTRY.read_text(encoding="utf-8")
    widget = WIDGET_JS.read_text(encoding="utf-8")

    assert "data-live-voice-mode" in text
    assert "widget.setAttribute('data-live-voice-mode', 'off')" in text
    assert "widget.setAttribute('data-live-voice-mode', 'edge-card')" not in text
    assert "widget.setAttribute('data-live-voice-ws-path', '/webchat/live/ws')" not in text
    assert "var liveVoiceMode = (script.getAttribute('data-live-voice-mode') || 'off').toLowerCase();" in widget
    combined = text + "\n" + widget
    assert "token=" not in combined
    assert "LIVE_VOICE_UPSTREAM_TOKEN" not in combined
    assert "47.87.143.41" not in combined
    assert "__SPEEDAF" not in combined
    assert "console.log" not in combined
    assert "[Speedaf Voice]" not in combined


def test_candidate_smoke_can_prove_live_voice_health_and_websocket_upgrade():
    smoke = CANDIDATE_SMOKE.read_text(encoding="utf-8")

    assert "CHECK_LIVE_VOICE_HEALTH" in smoke
    assert "CHECK_LIVE_VOICE_WS_UPGRADE" in smoke
    assert "Sec-WebSocket-Key" in smoke
    assert "Sec-WebSocket-Version: 13" in smoke
    assert "status_code != 101" in smoke
    assert "upgrade_header.lower() != 'websocket'" in smoke
