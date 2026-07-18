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


def test_controlled_topology_excludes_live_voice_credentials_by_default():
    text = COMPOSE.read_text(encoding="utf-8")
    runbook = RUNBOOK.read_text(encoding="utf-8")

    assert "LIVE_VOICE_UPSTREAM_TOKEN_HOST_PATH" not in text
    assert "LIVE_VOICE_UPSTREAM_TOKEN_FILE" not in text
    assert "/run/nexus/live_voice_token" not in text
    assert "use a bounded deployment override" in runbook
    assert "only into `app-controlled`" in runbook
    assert "must not be merged into the disabled base topology" in runbook
