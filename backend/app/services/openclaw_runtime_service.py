from __future__ import annotations

import os
from typing import Any

from ..schemas import OpenClawConnectivityProbeRead
from ..settings import get_settings
from .openclaw_client_factory import OpenClawBridgeHTTPClient, OpenClawBridgeHTTPError
from .openclaw_mcp_client import OpenClawMCPClient, OpenClawMCPError

settings = get_settings()


def _csv(raw: str | None) -> list[str]:
    return [item.strip() for item in (raw or '').split(',') if item.strip()]


def _truthy(raw: str | None) -> bool:
    return (raw or '').strip().lower() in {'1', 'true', 'yes', 'on'}


def _append_bridge_contract_warnings(warnings: list[str]) -> None:
    if not settings.openclaw_bridge_enabled:
        warnings.append('OpenClaw bridge is disabled; NexusDesk will not use the bridge-first runtime path')
        return

    warnings.append(f'OpenClaw bridge enabled at {settings.openclaw_bridge_url}')
    scopes = _csv(os.getenv('OPENCLAW_BRIDGE_GATEWAY_SCOPES', 'operator.read'))
    allow_writes = _truthy(os.getenv('OPENCLAW_BRIDGE_ALLOW_WRITES', 'false'))

    if allow_writes:
        warnings.append('OpenClaw bridge write mode is enabled; verify write scope and outbound safety gates before production sends')
        if 'operator.write' not in scopes:
            warnings.append('OpenClaw bridge write mode is enabled but OPENCLAW_BRIDGE_GATEWAY_SCOPES does not include operator.write')
    else:
        warnings.append('OpenClaw bridge is read-only; inbound sync can work, but outbound sends through the bridge will fail safely with bridge_writes_disabled')

    if settings.enable_outbound_dispatch and settings.outbound_provider == 'openclaw' and not allow_writes:
        warnings.append('Outbound provider is OpenClaw but bridge writes are disabled; production sends will remain failed/retryable')
    if settings.openclaw_cli_fallback_enabled:
        warnings.append('OPENCLAW_CLI_FALLBACK_ENABLED is true; production should keep CLI fallback disabled')


def probe_openclaw_connectivity() -> OpenClawConnectivityProbeRead:
    warnings: list[str] = []
    if settings.openclaw_deployment_mode == "disabled":
        warnings.append("OpenClaw deployment mode is disabled")
    if settings.openclaw_transport != "mcp":
        warnings.append("OpenClaw transport is not MCP; live same-route bridge checks are limited")
    if getattr(settings, 'openclaw_extra_paths', None):
        warnings.append("OPENCLAW_EXTRA_PATHS is configured for MCP command lookup")
    _append_bridge_contract_warnings(warnings)

    result = OpenClawConnectivityProbeRead(
        deployment_mode=settings.openclaw_deployment_mode,
        transport=settings.openclaw_transport,
        command=settings.openclaw_mcp_command,
        url=settings.openclaw_bridge_url if settings.openclaw_bridge_enabled else (settings.openclaw_mcp_url or None),
        token_auth_configured=bool(settings.openclaw_mcp_token_file),
        password_auth_configured=bool(settings.openclaw_mcp_password_file),
        bridge_started=False,
        conversations_tool_ok=False,
        conversations_seen=0,
        sample_session_key=None,
        warnings=warnings,
    )

    if settings.openclaw_deployment_mode == "disabled":
        return result

    if settings.openclaw_deployment_mode == "remote_gateway" and settings.openclaw_bridge_enabled:
        try:
            with OpenClawBridgeHTTPClient() as client:
                payload = client.conversations_list(limit=1, agent='support')
            result.bridge_started = True
            result.conversations_tool_ok = True
            conversations = _as_items(payload)
            result.conversations_seen = len(conversations)
            if conversations:
                result.sample_session_key = _session_key_from_item(conversations[0])
            else:
                result.warnings.append("Remote OpenClaw bridge is reachable but no routed conversations are currently visible")
        except OpenClawBridgeHTTPError as exc:
            result.warnings.append(f"openclaw_bridge_unreachable: {exc}")
        except Exception as exc:  # pragma: no cover
            result.warnings.append(f"unexpected_openclaw_bridge_probe_failure: {exc}")
        return result

    if settings.openclaw_transport != "mcp":
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
