from __future__ import annotations

from typing import Any

from ..schemas import OpenClawConnectivityProbeRead
from ..settings import get_settings
from .openclaw_mcp_client import OpenClawMCPClient, OpenClawMCPError

settings = get_settings()


def probe_openclaw_connectivity() -> OpenClawConnectivityProbeRead:
    warnings: list[str] = []
    if settings.openclaw_deployment_mode == "disabled":
        warnings.append("OpenClaw deployment mode is disabled")
    if settings.openclaw_transport != "mcp":
        warnings.append("OpenClaw transport is not MCP; live same-route bridge checks are limited")
    if getattr(settings, 'openclaw_extra_paths', None):
        warnings.append("OPENCLAW_EXTRA_PATHS is configured for MCP command lookup")

    result = OpenClawConnectivityProbeRead(
        deployment_mode=settings.openclaw_deployment_mode,
        transport=settings.openclaw_transport,
        command=settings.openclaw_mcp_command,
        url=settings.openclaw_mcp_url or None,
        token_auth_configured=bool(settings.openclaw_mcp_token_file),
        password_auth_configured=bool(settings.openclaw_mcp_password_file),
        bridge_started=False,
        conversations_tool_ok=False,
        conversations_seen=0,
        sample_session_key=None,
        warnings=warnings,
    )

    if settings.openclaw_transport != "mcp" or settings.openclaw_deployment_mode == "disabled":
        return result

    try:
        with OpenClawMCPClient() as client:
            result.bridge_started = True
            payload = client.conversations_list(limit=1, include_last_message=False)
            result.conversations_tool_ok = True
            conversations = _as_items(payload)
            result.conversations_seen = len(conversations)
            if conversations:
                result.sample_session_key = _session_key_from_item(conversations[0])
            if not conversations:
                result.warnings.append("Bridge is reachable but no routed conversations are currently visible")
    except FileNotFoundError as exc:
        result.warnings.append(f"OpenClaw CLI not found: {exc}")
    except OpenClawMCPError as exc:
        result.warnings.append(str(exc))
    except Exception as exc:  # pragma: no cover
        result.warnings.append(f"Unexpected OpenClaw probe failure: {exc}")
    return result


def _as_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("conversations", "items", "results", "content"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return [payload]
    return []


def _session_key_from_item(item: dict[str, Any]) -> str | None:
    for key in ("session_key", "sessionKey", "id"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return None
