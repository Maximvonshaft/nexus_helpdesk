from __future__ import annotations

from ..schemas import ExternalChannelConnectivityProbeRead
from ..settings import get_settings


def probe_external_channel_connectivity() -> ExternalChannelConnectivityProbeRead:
    """Compatibility status for legacy /api/admin/external_channel/connectivity-check.

    ExternalChannel is no longer part of the default Nexus runtime, so this endpoint
    must not open bridge, MCP, or CLI connections. It remains only to keep older
    admin frontends and automation from breaking while route names are migrated.
    """

    settings = get_settings()
    warnings = ["Legacy ExternalChannel runtime is disabled; Nexus now uses provider_runtime/native channels."]
    if settings.external_channel_deployment_mode != "disabled" or settings.external_channel_transport != "disabled":
        warnings.append("Legacy ExternalChannel env vars are still set, but active connectivity probing has been retired.")

    return ExternalChannelConnectivityProbeRead(
        deployment_mode=settings.external_channel_deployment_mode,
        transport=settings.external_channel_transport,
        command=settings.external_channel_mcp_command,
        url=None,
        token_auth_configured=False,
        password_auth_configured=False,
        bridge_started=False,
        conversations_tool_ok=False,
        conversations_seen=0,
        sample_session_key=None,
        warnings=warnings,
    )
