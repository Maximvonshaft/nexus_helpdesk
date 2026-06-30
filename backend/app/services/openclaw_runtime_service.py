from __future__ import annotations

from ..schemas import OpenClawConnectivityProbeRead
from ..settings import get_settings


def probe_openclaw_connectivity() -> OpenClawConnectivityProbeRead:
    """Compatibility status for legacy /api/admin/openclaw/connectivity-check.

    OpenClaw is no longer part of the default Nexus runtime, so this endpoint
    must not open bridge, MCP, or CLI connections. It remains only to keep older
    admin frontends and automation from breaking while route names are migrated.
    """

    settings = get_settings()
    warnings = ["Legacy OpenClaw runtime is disabled; Nexus now uses provider_runtime/native channels."]
    if settings.openclaw_deployment_mode != "disabled" or settings.openclaw_transport != "disabled":
        warnings.append("Legacy OpenClaw env vars are still set, but active connectivity probing has been retired.")

    return OpenClawConnectivityProbeRead(
        deployment_mode=settings.openclaw_deployment_mode,
        transport=settings.openclaw_transport,
        command=settings.openclaw_mcp_command,
        url=None,
        token_auth_configured=False,
        password_auth_configured=False,
        bridge_started=False,
        conversations_tool_ok=False,
        conversations_seen=0,
        sample_session_key=None,
        warnings=warnings,
    )
