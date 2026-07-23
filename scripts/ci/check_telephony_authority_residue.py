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
    "backend/app/static/webchat/voice-redirect.js",
    "scripts/_temporary_apply_canonical_telephony.py.gz.b64",
    "scripts/_temporary_telephony_backend_convergence.py",
)

REQUIRED_PATHS = (
    "backend/app/api/telephony.py",
    "backend/app/api/webchat_voice.py",
    "backend/app/livekit_agent_config.py",
    "backend/app/livekit_agent_worker.py",
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
    "backend/tests/test_livekit_agent_worker.py",
    "deploy/docker-compose.controlled.yml",
    "docs/runbooks/canonical-livekit-telephony.md",
    "webapp/src/features/webcall/WebCallPage.tsx",
    "webapp/src/features/channels/TelephonyConfigurationPanel.tsx",
)

FORBIDDEN_MARKERS = (
    "/webchat/live/ws",
    "/webchat/live",
    "/webcall-ai",
    "/webchat/voice/",
    "voice-redirect.js",
    "LIVE_VOICE_UPSTREAM_",
    "WEBCALL_AI_",
    "WEBCHAT_VOICE_ENABLED",
    "NEXUS_VOICE_TRANSFER_LLM_MODEL",
    "WarmTransferTask",
    "livekit.agents.beta",
    "nexus_media_edge",
    "provider_adapter_pending",
    "not_executed",
    "data-live-voice-ws-path",
    "edge-card",
    "live-voice-capture-worklet",
    "livekit_telephony_service",
    "webchat_live_voice",
    "webcall_ai_production",
    "webchat_voice_service",
    "from .event_service import",
    "WebchatVoiceSession.accepted_by_user_id",
    "temporary_telephony",
    "canonical_telephony_finalizer",
    "telephony_payload",
    "human_firstfalse",
    "/proc/1/cmdline",
)

SCAN_ROOTS = (
    ".github/workflows",
    "backend/app",
    "backend/scripts",
    "backend/.env.example",
    "deploy",
    "docs/runbooks",
    "docs/webcall-architecture.md",
    "docs/webchat-voice-readiness-audit.md",
    "docs/webchat-voice-runtime.md",
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


def _require_marker(findings: list[str], path: str, marker: str) -> None:
    source_path = ROOT / path
    if not source_path.is_file():
        return
    source = source_path.read_text(encoding="utf-8")
    if marker not in source:
        findings.append(f"canonical telephony marker missing {marker!r}: {path}")


def _forbid_marker(findings: list[str], path: str, marker: str) -> None:
    source_path = ROOT / path
    if not source_path.is_file():
        return
    source = source_path.read_text(encoding="utf-8")
    if marker in source:
        findings.append(f"parallel telephony authority marker {marker!r}: {path}")


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

    worker_path = "backend/app/livekit_agent_worker.py"
    compose_path = "deploy/docker-compose.controlled.yml"
    requirements_path = "backend/requirements.txt"
    _require_marker(findings, worker_path, '"/api/telephony/internal/agent-turn"')
    _require_marker(findings, worker_path, 'AgentServer(host="127.0.0.1", port=8081)')
    _require_marker(findings, worker_path, 'event_type="controller.heartbeat"')
    _require_marker(findings, worker_path, "publish_dtmf")
    _require_marker(findings, worker_path, '"warm_transfer_complete"')
    _require_marker(findings, worker_path, '"warm_transfer_cancel"')
    _forbid_marker(findings, worker_path, "inference.LLM")
    _forbid_marker(findings, worker_path, "livekit.plugins.openai")
    _forbid_marker(findings, worker_path, "livekit.plugins.anthropic")
    _forbid_marker(findings, worker_path, "ProviderRuntimeRouter")
    _require_marker(findings, compose_path, "livekit-agent-controlled:")
    _require_marker(findings, compose_path, "- telephony")
    _require_marker(findings, compose_path, "app.livekit_agent_worker")
    _require_marker(findings, compose_path, "http://127.0.0.1:8081/")
    _forbid_marker(findings, compose_path, "NEXUS_VOICE_TRANSFER_LLM_MODEL")
    _require_marker(findings, requirements_path, "livekit-agents==1.6.6")

    if findings:
        print("\n".join(findings))
        return 1
    print("canonical telephony authority: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
