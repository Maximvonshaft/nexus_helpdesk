#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

RETIRED_PATHS = (
    "backend/app/api/webchat_live_voice.py",
    "backend/app/services/live_voice_orchestration_service.py",
    "backend/app/services/livekit_telephony_service.py",
    "backend/app/services/webchat_voice_service.py",
    "backend/app/static/webchat/live-voice-capture-worklet.js",
    "scripts/_temporary_apply_canonical_telephony.py.gz.b64",
    "scripts/_temporary_telephony_backend_convergence.py",
)

REQUIRED_PATHS = (
    "backend/app/api/telephony.py",
    "backend/app/api/webchat_voice.py",
    "backend/app/services/telephony_configuration_service.py",
    "backend/app/services/telephony_event_service.py",
    "backend/app/services/telephony_projection_service.py",
    "backend/app/services/telephony_outbound_service.py",
    "backend/app/services/voice_session_service.py",
    "backend/app/services/voice_room_control_service.py",
    "backend/app/services/voice_command_service.py",
    "backend/app/services/voice_command_dispatcher.py",
    "backend/app/services/livekit_agent_turn_service.py",
    "backend/app/services/livekit_voice_provider.py",
    "backend/app/services/agent_routing_service.py",
    "backend/app/services/agent_availability_service.py",
    "backend/app/services/agent_confirmation_service.py",
    "webapp/src/features/webcall/WebCallPage.tsx",
    "webapp/src/features/channels/TelephonyConfigurationPanel.tsx",
)

FORBIDDEN_MARKERS = (
    "/webchat/live/ws",
    "LIVE_VOICE_UPSTREAM_",
    "nexus_media_edge",
    "provider_adapter_pending",
    "not_executed",
    "data-live-voice-ws-path",
    "edge-card",
    "live-voice-capture-worklet",
    "livekit_telephony_service",
    "webchat_voice_service",
    "WebchatVoiceSession.accepted_by_user_id",
    "temporary_telephony",
    "canonical_telephony_finalizer",
    "telephony_payload",
)

SCAN_ROOTS = (
    "backend/app",
    "backend/.env.example",
    "deploy",
    "webapp/src",
)

TEXT_SUFFIXES = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".mjs",
    ".json",
    ".yaml",
    ".yml",
    ".md",
    ".sh",
    ".conf",
    ".template",
    ".example",
}


def main() -> int:
    findings: list[str] = []
    for relative in RETIRED_PATHS:
        if (ROOT / relative).exists():
            findings.append(f"retired telephony path exists: {relative}")
    for relative in REQUIRED_PATHS:
        if not (ROOT / relative).is_file():
            findings.append(
                f"canonical telephony authority missing: {relative}"
            )

    candidates: list[Path] = []
    for relative in SCAN_ROOTS:
        path = ROOT / relative
        if path.is_file():
            candidates.append(path)
        elif path.is_dir():
            candidates.extend(
                item
                for item in path.rglob("*")
                if item.is_file()
                and item.suffix.lower() in TEXT_SUFFIXES
            )
    for path in sorted(set(candidates)):
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for marker in FORBIDDEN_MARKERS:
            if marker in source:
                findings.append(
                    "retired telephony marker "
                    f"{marker!r}: {path.relative_to(ROOT)}"
                )
    if findings:
        print("\n".join(findings))
        return 1
    print("canonical telephony authority: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
