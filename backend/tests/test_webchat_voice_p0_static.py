from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OBSERVABILITY = ROOT / "backend" / "app" / "services" / "observability.py"
VOICE_SESSION = ROOT / "backend" / "app" / "services" / "voice_session_service.py"
VOICE_ROUTING = ROOT / "backend" / "app" / "services" / "agent_routing_service.py"
VOICE_COMMANDS = ROOT / "backend" / "app" / "services" / "voice_command_dispatcher.py"
VOICE_EVIDENCE = ROOT / "backend" / "app" / "services" / "voice_evidence_service.py"
VOICE_API = ROOT / "backend" / "app" / "api" / "webchat_voice.py"
TELEPHONY_API = ROOT / "backend" / "app" / "api" / "telephony.py"
RATE_LIMIT = ROOT / "backend" / "app" / "services" / "webchat_rate_limit.py"
WEBCHAT_ROUTE = ROOT / "webapp" / "src" / "routes" / "webchat.tsx"
WORKSPACE = ROOT / "webapp" / "src" / "features" / "operator-workspace" / "OperatorWorkspacePage.tsx"
WORKSPACE_CONVERSATION = ROOT / "webapp" / "src" / "features" / "operator-workspace" / "OperatorWorkspaceConversation.tsx"
WORKSPACE_CASE = ROOT / "webapp" / "src" / "features" / "operator-workspace" / "OperatorWorkspaceCase.tsx"
WEBCALL_PAGE = ROOT / "webapp" / "src" / "features" / "webcall" / "WebCallPage.tsx"
VOICE_ENTRY = ROOT / "backend" / "app" / "static" / "webchat" / "voice-entry.js"
WIDGET_JS = ROOT / "backend" / "app" / "static" / "webchat" / "widget.js"
RESIDUE_GATE = ROOT / "scripts" / "ci" / "check_telephony_authority_residue.py"


def test_backend_voice_authorities_and_metrics_are_present():
    obs = OBSERVABILITY.read_text(encoding="utf-8")
    session = VOICE_SESSION.read_text(encoding="utf-8")
    routing = VOICE_ROUTING.read_text(encoding="utf-8")
    commands = VOICE_COMMANDS.read_text(encoding="utf-8")
    api = VOICE_API.read_text(encoding="utf-8")
    telephony = TELEPHONY_API.read_text(encoding="utf-8")

    assert "nexusdesk_voice_session_events_total" in obs
    assert "nexusdesk_voice_provider_errors_total" in obs
    assert "nexusdesk_voice_call_duration_seconds" in obs
    assert "nexusdesk_voice_ringing_duration_seconds" in obs
    assert "list_admin_incoming_voice_sessions" in session
    assert "accept_voice_offer" in session
    assert "decline_voice_offer" in session
    assert "VoiceRoutingOffer" in routing
    assert "dispatch_pending_voice_commands" in commands
    assert '"/admin/voice/sessions"' in api
    assert '"/admin/voice/{voice_session_id}/reject"' in api
    assert '"/livekit/webhook"' in telephony
    assert '"/livekit/controller-events"' in telephony


def test_widget_opens_one_livekit_room_path_and_old_pcm_path_is_absent():
    webchat_route = WEBCHAT_ROUTE.read_text(encoding="utf-8")
    workspace = WORKSPACE.read_text(encoding="utf-8")
    conversation = WORKSPACE_CONVERSATION.read_text(encoding="utf-8")
    widget = WIDGET_JS.read_text(encoding="utf-8")
    webcall = WEBCALL_PAGE.read_text(encoding="utf-8")

    assert "WebchatCompatibilityRedirect" in webchat_route
    assert "operatorWorkspaceApi.reply" in conversation
    assert "暂无客户沟通" in conversation
    assert "回复和接手处理暂不可用" in conversation
    assert "livekit-room" in widget
    assert "/voice/sessions" in widget
    assert "/webcall/" in widget
    assert "LiveKit" in webcall
    assert "/webchat/live/ws" not in widget
    assert "AudioWorklet" not in widget
    assert "LIVE_VOICE_UPSTREAM" not in widget
    assert "visitor_token" not in workspace
    assert "LIVEKIT_API_SECRET" not in workspace


def test_voice_evidence_uses_canonical_case_and_transcript_storage_without_secrets():
    case_source = WORKSPACE_CASE.read_text(encoding="utf-8")
    evidence = VOICE_EVIDENCE.read_text(encoding="utf-8")
    webcall = WEBCALL_PAGE.read_text(encoding="utf-8")

    assert "EvidencePanel" in case_source
    assert "evidence_timeline" in case_source
    assert "WebchatVoiceTranscriptSegment" in evidence
    assert "WebchatVoiceAITurn" in evidence
    assert "WebchatVoiceAIAction" in evidence
    assert "participant_token" not in evidence
    assert "LIVEKIT_API_SECRET" not in evidence
    assert "window.location.hash" in webcall
    assert "history.replaceState" in webcall


def test_voice_entry_delegates_to_consolidated_widget():
    entry = VOICE_ENTRY.read_text(encoding="utf-8")
    widget = WIDGET_JS.read_text(encoding="utf-8")

    assert "/webchat/widget.js" in entry
    assert "window.__NEXUSDESK_WEBCHAT_LOADED__" in entry
    assert "data-live-voice-mode" in entry
    assert "startLiveVoice" in widget
    assert "stopLiveVoice" in widget


def test_retired_telephony_paths_are_permanently_gated():
    gate = RESIDUE_GATE.read_text(encoding="utf-8")
    retired_files = (
        ROOT / "backend" / "app" / "services" / "webchat_voice_service.py",
        ROOT / "backend" / "app" / "services" / "live_voice_orchestration_service.py",
        ROOT / "backend" / "app" / "api" / "webchat_live_voice.py",
        ROOT / "backend" / "app" / "static" / "webchat" / "live-voice-capture-worklet.js",
    )
    assert not any(path.exists() for path in retired_files)
    for marker in (
        "LIVE_VOICE_UPSTREAM",
        "/webchat/live/ws",
        "nexus_media_edge",
        "provider_adapter_pending",
        "not_executed",
        "temporary_telephony",
    ):
        assert marker in gate


def test_database_rate_limit_resets_expired_bucket_instead_of_duplicate_insert():
    rate_limit = RATE_LIMIT.read_text(encoding="utf-8")

    assert "if existing is None:" in rate_limit
    assert 'if existing["window_start"] is None or existing["window_start"] < window_start:' in rate_limit
    assert "SET window_start = :window_start, request_count = 1, updated_at = :updated_at" in rate_limit
    assert "UPDATE webchat_rate_limits" in rate_limit
    reset_block = rate_limit.split(
        'if existing["window_start"] is None or existing["window_start"] < window_start:',
        1,
    )[1].split("request_count = int", 1)[0]
    assert "INSERT INTO webchat_rate_limits" not in reset_block
