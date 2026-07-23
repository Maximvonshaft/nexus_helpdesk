from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = ROOT / "docs" / "runbooks" / "canonical-livekit-telephony.md"
COMPOSE = ROOT / "deploy" / "docker-compose.controlled.yml"
RETIRED_RUNBOOK = (
    ROOT
    / "docs"
    / "runbooks"
    / "webchat-live-voice-edge-credential-rotation.md"
)


def test_canonical_runbook_is_fail_closed_and_secret_safe():
    text = RUNBOOK.read_text(encoding="utf-8")
    for marker in (
        "LiveKit as the only real-time media plane",
        "A phone call does not require a Ticket",
        "Mock provider success is prohibited in production",
        "Use secret files in production rather than inline secret values",
        "LIVEKIT_API_KEY_FILE=/run/secrets/livekit_api_key",
        "LIVEKIT_API_SECRET_FILE=/run/secrets/livekit_api_secret",
        "Tenant is never accepted from the browser or Provider payload",
        "A model-supplied boolean is never trusted as customer consent",
        "Operator decline/offer expiry",
        "do not hang up caller",
        "Do not claim production PSTN activation",
    ):
        assert marker in text
    assert "Bearer " not in text
    assert "LIVE_VOICE_UPSTREAM" not in text
    assert "LIVE_VOICE_TOKEN" not in text
    assert not RETIRED_RUNBOOK.exists()


def test_controlled_topology_contains_no_retired_media_edge_credentials():
    text = COMPOSE.read_text(encoding="utf-8")
    for marker in (
        "LIVE_VOICE_TOKEN",
        "LIVE_VOICE_UPSTREAM",
        "nexus_media_edge",
        "/run/nexus/live_voice_token",
    ):
        assert marker not in text
