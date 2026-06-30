from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VOICE_ENTRY = ROOT / "backend" / "app" / "static" / "webchat" / "voice-entry.js"
DEMO_HTML = ROOT / "backend" / "app" / "static" / "webchat" / "demo.html"
DEMO_INDEX = ROOT / "backend" / "app" / "static" / "webchat" / "demo" / "index.html"
DEMO_APP_JS = ROOT / "backend" / "app" / "static" / "webchat" / "demo" / "js" / "app.js"
ADMIN_ROUTE = ROOT / "webapp" / "src" / "routes" / "webchat-voice.tsx"
OPERATOR_ROUTE = ROOT / "webapp" / "src" / "routes" / "webcall-operator.tsx"
AGENT_PANEL = ROOT / "webapp" / "src" / "components" / "webcall" / "AgentWebCallPanel.tsx"
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


def test_showcase_loads_feature_gated_webcall_entry():
    redirect_text = DEMO_HTML.read_text(encoding="utf-8")
    index_text = DEMO_INDEX.read_text(encoding="utf-8")

    assert "url=/webchat/demo/" in redirect_text
    assert "/webchat/voice-entry.js" in index_text
    assert "data-tenant=\"default\"" in index_text
    assert "data-channel=\"website\"" in index_text
    assert "data-title=\"Speedaf WebCall\"" in index_text
    assert "data-voice-label=\"WebCall\"" in index_text
    assert "data-live-voice-mode=\"edge-card\"" in index_text
    assert "data-live-voice-ws-path=\"/webchat/live/ws\"" in index_text


def test_showcase_support_chat_auto_opens_on_page_load():
    index_text = DEMO_INDEX.read_text(encoding="utf-8")
    app_text = DEMO_APP_JS.read_text(encoding="utf-8")

    assert 'id="floatingChat"' in index_text
    assert 'id="chatPanel"' in index_text
    assert 'class="chat-panel is-closed"' in index_text
    assert "js/app.js?v=nexus-auto-open-chat-20260630" in index_text
    assert "openChat();\n\n  function openChat()" in app_text


def test_public_voice_entry_contains_feature_gated_edge_card_without_runtime_secrets():
    text = VOICE_ENTRY.read_text(encoding="utf-8")

    assert "data-live-voice-mode" in text
    assert "edge-card" in text
    assert "/webchat/live/ws" in text
    assert "createScriptProcessor" in text
    assert "token=" not in text
    assert "47.87.143.41" not in text
    assert "__SPEEDAF" not in text
    assert "console.log" not in text
    assert "[Speedaf Voice]" not in text


def test_agent_webcall_console_route_is_registered():
    route_text = ADMIN_ROUTE.read_text(encoding="utf-8")
    operator_text = OPERATOR_ROUTE.read_text(encoding="utf-8")
    router_text = ROUTER.read_text(encoding="utf-8")

    assert "path: '/webchat-voice'" in route_text
    assert "WebCall Agent Console" in route_text
    assert "path: '/webcall'" in operator_text
    assert "WebCall Operator Workbench" in operator_text
    assert "api.webchatVoiceIncomingSessions" in operator_text
    assert "api.webchatHandoffQueue" in operator_text
    assert "api.webcallAIDemoStatus" in operator_text
    assert "AgentWebCallPanel" in route_text
    assert "AgentWebCallPanel" in operator_text
    assert "Mock voice session" not in route_text
    assert "Mock voice session" not in operator_text
    assert "Accept mock call" not in route_text
    assert "Accept mock call" not in operator_text
    assert "End mock call" not in route_text
    assert "End mock call" not in operator_text
    assert "WebchatVoiceRoute" in router_text
    assert "WebCallOperatorRoute" in router_text
    assert "@/routes/webchat-voice" in router_text
    assert "@/routes/webcall-operator" in router_text


def test_agent_webcall_panel_uses_livekit_click_to_accept_only():
    panel_text = AGENT_PANEL.read_text(encoding="utf-8")
    package_text = PACKAGE_JSON.read_text(encoding="utf-8")
    component_prefix, accept_body = panel_text.split("const acceptMutation", 1)

    assert "Incoming WebCall" in panel_text
    assert "Accept WebCall" in panel_text
    assert "End WebCall" in panel_text
    assert "webchatVoiceApi.acceptSession" in panel_text
    assert "webchatVoiceApi.endSession" in panel_text
    assert "runtimeConfig" in panel_text
    assert "RoomEvent" in panel_text
    assert "room.connect" in panel_text
    assert "publishTrack" in panel_text
    assert "createLocalAudioTrack" in panel_text
    assert "await createLocalAudioTrack" in accept_body
    assert "await createLocalAudioTrack" not in component_prefix
    assert "console.log" not in panel_text
    assert "participant_token" not in panel_text.replace("accepted.participant_token", "")
    assert "LIVEKIT_API_SECRET" not in panel_text
    assert "LIVEKIT_API_KEY" not in panel_text
    assert "livekit-client" in package_text

    forbidden_terms = ["sip", "pstn", "twilio", "vonage", "mediarecorder", "recording_enabled", "transcription"]
    lowered = panel_text.lower()
    assert not any(term in lowered for term in forbidden_terms)


def test_agent_webcall_panel_has_lifecycle_safe_operator_errors():
    panel_text = AGENT_PANEL.read_text(encoding="utf-8")

    assert "already accepted by another agent" in panel_text
    assert "该通话已被其他客服接起" in panel_text
    assert "该来电已超时" in panel_text
    assert "该通话已结束" in panel_text
    assert "该通话已取消" in panel_text
    assert "该通话已失败" in panel_text
    assert "Unknown / 未知状态" in panel_text
    assert "readableAcceptError" in panel_text
    assert "raw provider" not in panel_text.lower()
    assert "secret" not in panel_text.lower()
    assert "visitor_token" not in panel_text


def test_visitor_webcall_route_is_registered_and_click_to_join_only():
    route_text = WEBCALL_ROUTE.read_text(encoding="utf-8")
    router_text = ROUTER.read_text(encoding="utf-8")
    package_text = PACKAGE_JSON.read_text(encoding="utf-8")
    module_prefix, join_body = route_text.split("function joinCall", 1)

    assert "path: '/webcall/$voice_session_id'" in route_text
    assert "createLocalAudioTrack" in route_text
    assert "RoomEvent" in route_text
    assert "room.connect" in route_text
    assert "publishTrack" in route_text
    assert "history.replaceState" in route_text
    assert "Microphone permission will be requested only after you click Join" in route_text
    assert "no recording in this phase" in route_text.lower()
    assert "await createLocalAudioTrack" in join_body
    assert "await createLocalAudioTrack" not in module_prefix
    assert "WebCallRoute" in router_text
    assert "@/routes/webcall" in router_text
    assert "livekit-client" in package_text
    forbidden_terms = ["sip", "pstn", "twilio", "vonage", "recording_enabled", "transcription"]
    lowered = route_text.lower()
    assert not any(term in lowered for term in forbidden_terms)
