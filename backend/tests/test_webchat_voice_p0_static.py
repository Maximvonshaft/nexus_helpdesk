from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OBSERVABILITY = ROOT / "backend" / "app" / "services" / "observability.py"
VOICE_SERVICE = ROOT / "backend" / "app" / "services" / "webchat_voice_service.py"
VOICE_API = ROOT / "backend" / "app" / "api" / "webchat_voice.py"
RATE_LIMIT = ROOT / "backend" / "app" / "services" / "webchat_rate_limit.py"
AGENT_PANEL = ROOT / "webapp" / "src" / "components" / "webcall" / "AgentWebCallPanel.tsx"
AGENT_ROUTE = ROOT / "webapp" / "src" / "routes" / "webchat-voice.tsx"
WEBCHAT_ROUTE = ROOT / "webapp" / "src" / "routes" / "webchat.tsx"
WEBCHAT_INBOX_V5 = ROOT / "webapp" / "src" / "features" / "webchat-inbox-v5" / "WebchatInboxV5Page.tsx"
WORKSPACE_ROUTE = ROOT / "webapp" / "src" / "routes" / "workspace.tsx"
VOICE_ENTRY = ROOT / "backend" / "app" / "static" / "webchat" / "voice-entry.js"
WEBCALL_ROUTE = ROOT / "webapp" / "src" / "routes" / "webcall.tsx"


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
    assert '"/admin/tickets/{ticket_id}/voice/{voice_session_id}/reject"' in api


def test_frontend_p0_queue_reject_and_text_fallback_are_present():
    panel = AGENT_PANEL.read_text(encoding="utf-8")
    route = AGENT_ROUTE.read_text(encoding="utf-8")
    webchat_route = WEBCHAT_ROUTE.read_text(encoding="utf-8")
    webchat = WEBCHAT_INBOX_V5.read_text(encoding="utf-8")
    entry = VOICE_ENTRY.read_text(encoding="utf-8")
    webcall = WEBCALL_ROUTE.read_text(encoding="utf-8")

    assert "Reject WebCall" in panel
    assert "webchatVoiceApi.rejectSession" in panel
    assert "Rejecting WebCall" in panel
    assert "Incoming WebCall Queue" in route
    assert "webchatVoiceApi.incomingSessions" in route
    assert "WebchatInboxV5Page" in webchat_route
    assert "AgentWebCallPanel" in webchat
    assert "api.webchatVoiceIncomingSessions" in webchat
    assert "Incoming WebCall" in webchat
    assert "Continue in WebChat text support" in entry
    assert "textFallbackMessage" in entry
    assert "Continue with WebChat text" in webcall
    assert "visitor_token" not in panel
    assert "LIVEKIT_API_SECRET" not in panel

def test_voice_call_evidence_cards_are_present_and_do_not_render_secrets():
    webchat_route = WEBCHAT_ROUTE.read_text(encoding="utf-8")
    webchat = WEBCHAT_INBOX_V5.read_text(encoding="utf-8")
    workspace = WORKSPACE_ROUTE.read_text(encoding="utf-8")
    combined = webchat + "\n" + workspace

    assert "WebchatInboxV5Page" in webchat_route
    assert "voice-call-evidence-card" in webchat
    assert "ticket-timeline-voice-call-evidence-card" in workspace
    for marker in [
        "voice_session_id",
        "provider",
        "accepted_by",
        "ended_by",
        "ringing_duration_seconds",
        "talk_duration_seconds",
        "total_duration_seconds",
        "recording_status",
        "transcript_status",
        "summary_status",
    ]:
        assert marker in combined

    evidence_blocks = combined.split("voice-call-evidence-card", 1)[-1] + combined.split("ticket-timeline-voice-call-evidence-card", 1)[-1]
    forbidden = ["participant_token", "visitor_token", "LIVEKIT_API_SECRET", "api_secret", "password"]
    assert not any(marker in evidence_blocks for marker in forbidden)


def test_voice_entry_has_click_cooldown_to_prevent_duplicate_incoming_calls():
    entry = VOICE_ENTRY.read_text(encoding="utf-8")

    assert "lastVoiceStartedAt" in entry
    assert "lastVoiceStartedKey" in entry
    assert "data-voice-cooldown-ms" in entry
    assert "cooldownRemainingMs" in entry
    assert "recordVoiceStarted" in entry
    assert "A WebCall was just started" in entry
    assert "wait " in entry and " seconds before starting another one" in entry


def test_database_rate_limit_resets_expired_bucket_instead_of_duplicate_insert():
    rate_limit = RATE_LIMIT.read_text(encoding="utf-8")

    assert "if existing is None:" in rate_limit
    assert 'if existing["window_start"] is None or existing["window_start"] < window_start:' in rate_limit
    assert "SET window_start = :window_start, request_count = 1, updated_at = :updated_at" in rate_limit

    expired_block = rate_limit.split('if existing["window_start"] is None or existing["window_start"] < window_start:', 1)[1]
    expired_block = expired_block.split("request_count = int", 1)[0]
    assert "UPDATE webchat_rate_limits" in expired_block
    assert "INSERT INTO webchat_rate_limits" not in expired_block
