from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OBSERVABILITY = ROOT / "backend" / "app" / "services" / "observability.py"
VOICE_SERVICE = ROOT / "backend" / "app" / "services" / "webchat_voice_service.py"
VOICE_API = ROOT / "backend" / "app" / "api" / "webchat_voice.py"
RATE_LIMIT = ROOT / "backend" / "app" / "services" / "webchat_rate_limit.py"
WEBCHAT_ROUTE = ROOT / "webapp" / "src" / "routes" / "webchat.tsx"
WORKSPACE = ROOT / "webapp" / "src" / "features" / "operator-workspace" / "OperatorWorkspacePage.tsx"
WORKSPACE_CONVERSATION = ROOT / "webapp" / "src" / "features" / "operator-workspace" / "OperatorWorkspaceConversation.tsx"
WORKSPACE_CASE = ROOT / "webapp" / "src" / "features" / "operator-workspace" / "OperatorWorkspaceCase.tsx"
VOICE_ENTRY = ROOT / "backend" / "app" / "static" / "webchat" / "voice-entry.js"
WIDGET_JS = ROOT / "backend" / "app" / "static" / "webchat" / "widget.js"


def test_backend_p0_routes_and_metrics_are_present():
    obs = OBSERVABILITY.read_text(encoding="utf-8")
    service = VOICE_SERVICE.read_text(encoding="utf-8")
    api = VOICE_API.read_text(encoding="utf-8")

    assert "nexusdesk_voice_session_events_total" in obs
    assert "nexusdesk_voice_provider_errors_total" in obs
    assert "nexusdesk_voice_call_duration_seconds" in obs
    assert "nexusdesk_voice_ringing_duration_seconds" in obs
    assert "list_admin_incoming_voice_sessions" in service
    assert "with managed_session(db):\n        return list_admin_incoming_voice_sessions" in api
    assert "db.commit()" not in service.split("def list_admin_incoming_voice_sessions", 1)[1].split("\ndef accept_admin_voice_session", 1)[0]
    assert "reject_admin_voice_session" in service
    assert "voice.session.rejected" in service
    assert '"/admin/voice/sessions"' in api
    assert '"/admin/voice/{voice_session_id}/reject"' in api


def test_canonical_workspace_provides_text_fallback_while_widget_owns_live_voice():
    webchat_route = WEBCHAT_ROUTE.read_text(encoding="utf-8")
    workspace = WORKSPACE.read_text(encoding="utf-8")
    conversation = WORKSPACE_CONVERSATION.read_text(encoding="utf-8")
    widget = WIDGET_JS.read_text(encoding="utf-8")

    assert "WebchatCompatibilityRedirect" in webchat_route
    assert "operatorWorkspaceApi.reply" in conversation
    assert "暂无客户沟通" in conversation
    assert "回复和接手处理暂不可用" in conversation
    assert "nd-webchat-voice" in widget
    assert "/webchat/live/ws" in widget
    assert "startLiveVoice" in widget
    assert "stopLiveVoice" in widget
    assert "visitor_token" not in workspace
    assert "LIVEKIT_API_SECRET" not in workspace


def test_voice_call_evidence_flows_through_canonical_case_evidence_without_secrets():
    case_source = WORKSPACE_CASE.read_text(encoding="utf-8")
    widget = WIDGET_JS.read_text(encoding="utf-8")

    assert "EvidencePanel" in case_source
    assert "evidence_timeline" in case_source
    assert "evidencePresentation" in case_source
    assert "OperatorTechnicalDisclosure" in case_source
    assert "系统信息" in case_source
    assert "JSON.stringify(entry.summary, null, 2)" in case_source
    assert "voiceStatus" in widget
    assert "addVoiceTranscript" in widget
    assert "nd-webchat-voice-transcript" in widget

    evidence_block = case_source.split("function EvidencePanel", 1)[-1]
    forbidden = ["participant_token", "visitor_token", "LIVEKIT_API_SECRET", "api_secret"]
    assert not any(marker in evidence_block for marker in forbidden)


def test_voice_entry_delegates_to_consolidated_widget():
    entry = VOICE_ENTRY.read_text(encoding="utf-8")
    widget = WIDGET_JS.read_text(encoding="utf-8")

    assert "/webchat/widget.js" in entry
    assert "window.__NEXUSDESK_WEBCHAT_LOADED__" in entry
    assert "data-live-voice-mode" in entry
    assert "startLiveVoice" in widget
    assert "stopLiveVoice" in widget


def test_database_rate_limit_resets_expired_bucket_instead_of_duplicate_insert():
    rate_limit = RATE_LIMIT.read_text(encoding="utf-8")

    assert "if existing is None:" in rate_limit
    assert 'if existing["window_start"] is None or existing["window_start"] < window_start:' in rate_limit
    assert "SET window_start = :window_start, request_count = 1, updated_at = :updated_at" in rate_limit
    assert "UPDATE webchat_rate_limits" in rate_limit
    assert "INSERT INTO webchat_rate_limits" not in rate_limit.split('if existing["window_start"] is None or existing["window_start"] < window_start:', 1)[1].split("request_count = int", 1)[0]
