#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RETIRED_PATHS = (
    "backend/app/api/webchat_live_voice.py",
    "backend/app/services/live_voice_orchestration_service.py",
    "backend/app/static/webchat/live-voice-capture-worklet.js",
)
REQUIRED_PATHS = (
    "backend/app/api/telephony.py",
    "backend/app/services/livekit_telephony_service.py",
    "backend/app/services/livekit_agent_turn_service.py",
    "backend/app/services/livekit_voice_provider.py",
    "backend/app/services/agent_routing_service.py",
    "webapp/src/features/webcall/WebCallPage.tsx",
    "webapp/src/features/channels/TelephonyConfigurationPanel.tsx",
)
FORBIDDEN_MARKERS = (
    "/webchat/live/ws",
    "LIVE_VOICE_UPSTREAM_",
    "nexus_media_edge",
    "provider_adapter_pending",
    "data-live-voice-ws-path",
    "edge-card",
    "live-voice-capture-worklet",
)
SCAN_ROOTS = (
    "backend/app",
    "backend/.env.example",
    "deploy",
    "webapp/src",
)


def main() -> int:
    findings: list[str] = []
    for relative in RETIRED_PATHS:
        if (ROOT / relative).exists():
            findings.append(f"retired telephony path exists: {relative}")
    for relative in REQUIRED_PATHS:
        if not (ROOT / relative).is_file():
            findings.append(f"canonical telephony authority missing: {relative}")
    candidates: list[Path] = []
    for relative in SCAN_ROOTS:
        path = ROOT / relative
        if path.is_file():
            candidates.append(path)
        elif path.is_dir():
            candidates.extend(
                item for item in path.rglob('*')
                if item.is_file() and item.suffix.lower() in {'.py', '.ts', '.tsx', '.js', '.mjs', '.json', '.yaml', '.yml', '.md', '.sh', '.conf', '.template', '.example'}
            )
    for path in sorted(set(candidates)):
        try:
            source = path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            continue
        for marker in FORBIDDEN_MARKERS:
            if marker in source:
                findings.append(f"retired telephony marker {marker!r}: {path.relative_to(ROOT)}")
    if findings:
        print('\n'.join(findings))
        return 1
    print('canonical telephony authority: clean')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
