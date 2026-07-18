from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = ROOT / "docs" / "runbooks" / "webchat-live-voice-edge-credential-rotation.md"
COMPOSE = ROOT / "deploy" / "docker-compose.controlled.yml"


def test_rotation_runbook_is_fail_closed_and_secret_safe():
    text = RUNBOOK.read_text(encoding="utf-8")
    for marker in (
        "does not authorize a production change",
        "WEBCHAT_VOICE_ENABLED=false",
        "PROVIDER_RUNTIME_ENABLED=false",
        "ENABLE_OUTBOUND_DISPATCH=false",
        "OPERATIONS_DISPATCH_MODE=disabled",
        "Do not print the token",
        "If the upstream has no read-only credential check, stop",
        "Restart only the service that consumes the credential",
        "None of these states implies another",
    ):
        assert marker in text
    assert "LIVE_VOICE_TOKEN=<" not in text
    assert "Bearer " not in text
    assert "curl -H 'Authorization" not in text


def test_controlled_topology_limits_live_voice_token_to_application():
    text = COMPOSE.read_text(encoding="utf-8")
    assert text.count("LIVE_VOICE_TOKEN_HOST_PATH") == 1
    app_start = text.index("  app-controlled:\n")
    outbound_start = text.index("  worker-outbound-controlled:\n")
    app_block = text[app_start:outbound_start]
    assert "LIVE_VOICE_TOKEN_HOST_PATH" in app_block
    assert "/run/nexus/live_voice_token:ro" in app_block
