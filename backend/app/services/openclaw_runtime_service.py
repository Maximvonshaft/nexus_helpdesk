from __future__ import annotations

from typing import Any, Callable

from ..schemas import OpenClawConnectivityProbeRead
from ..settings import get_settings
from .openclaw_mcp_client import OpenClawMCPClient, OpenClawMCPError

settings = get_settings()


def probe_openclaw_connectivity() -> OpenClawConnectivityProbeRead:
    """Probe OpenClaw gateway capability without sending customer messages.

    Levels:
    - L0: configuration only / disabled / unavailable
    - L1: bridge/client starts
    - L2: conversations list works
    - L3: transcript/messages read works
    - L4: same-route send prerequisites are present; no message is sent
    - L5: attachment metadata check works when supported
    """
    warnings: list[str] = []
    if settings.openclaw_deployment_mode == 'disabled':
        warnings.append('OpenClaw deployment mode is disabled')
    if settings.openclaw_transport != 'mcp':
        warnings.append('OpenClaw transport is not MCP; live same-route bridge checks are limited')
    if getattr(settings, 'openclaw_extra_paths', None):
        warnings.append('OPENCLAW_EXTRA_PATHS is configured for MCP command lookup')

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
        level='L0',
        transcript_read_ok=False,
        same_route_send_ready=False,
        attachment_metadata_ok=False,
        warnings=warnings,
    )

    if settings.openclaw_transport != 'mcp' or settings.openclaw_deployment_mode == 'disabled':
        return result

    try:
        with OpenClawMCPClient() as client:
            result.bridge_started = True
            result.level = 'L1'

            payload = client.conversations_list(limit=1, include_last_message=False)
            result.conversations_tool_ok = True
            conversations = _as_items(payload)
            result.conversations_seen = len(conversations)
            if conversations:
                sample = conversations[0]
                result.sample_session_key = _session_key_from_item(sample)
                result.level = 'L2'
                _probe_transcript_read(client, result)
                _probe_same_route_readiness(sample, result)
                _probe_attachment_metadata(client, result)
            else:
                result.warnings.append('Bridge is reachable but no routed conversations are currently visible')
    except FileNotFoundError as exc:
        result.warnings.append(f'OpenClaw CLI not found: {exc}')
    except OpenClawMCPError as exc:
        result.warnings.append(str(exc))
    except Exception as exc:  # pragma: no cover
        result.warnings.append(f'Unexpected OpenClaw probe failure: {exc}')
    return result


def _probe_transcript_read(client: Any, result: OpenClawConnectivityProbeRead) -> None:
    if not result.sample_session_key:
        result.warnings.append('Transcript read skipped because no sample session key is available')
        return
    call = _first_callable(client, ['conversation_messages', 'messages_list', 'conversation_read', 'transcript_read'])
    if call is None:
        result.warnings.append('Transcript read tool is not exposed by current OpenClaw MCP client')
        return
    try:
        call(result.sample_session_key, limit=1)
        result.transcript_read_ok = True
        result.level = 'L3'
    except TypeError:
        try:
            call(session_key=result.sample_session_key, limit=1)
            result.transcript_read_ok = True
            result.level = 'L3'
        except Exception as exc:
            result.warnings.append(f'Transcript read probe failed: {exc}')
    except Exception as exc:
        result.warnings.append(f'Transcript read probe failed: {exc}')


def _probe_same_route_readiness(sample: dict[str, Any], result: OpenClawConnectivityProbeRead) -> None:
    if not result.sample_session_key:
        return
    channel = sample.get('channel') or sample.get('provider') or sample.get('route', {}).get('channel') if isinstance(sample.get('route'), dict) else None
    recipient = sample.get('recipient') or sample.get('target') or sample.get('peer') or sample.get('contact')
    account_id = sample.get('account_id') or sample.get('accountId')
    if result.sample_session_key and (channel or recipient or account_id):
        result.same_route_send_ready = True
        if result.level in {'L2', 'L3'}:
            result.level = 'L4'
    else:
        result.warnings.append('Same-route send dry-run is not ready: missing route/account/recipient metadata')


def _probe_attachment_metadata(client: Any, result: OpenClawConnectivityProbeRead) -> None:
    call = _first_callable(client, ['attachments_list', 'attachment_metadata', 'attachments_metadata'])
    if call is None:
        return
    try:
        kwargs = {'session_key': result.sample_session_key} if result.sample_session_key else {}
        call(**kwargs)
        result.attachment_metadata_ok = True
        result.level = 'L5'
    except TypeError:
        try:
            call(result.sample_session_key)
            result.attachment_metadata_ok = True
            result.level = 'L5'
        except Exception as exc:
            result.warnings.append(f'Attachment metadata probe failed: {exc}')
    except Exception as exc:
        result.warnings.append(f'Attachment metadata probe failed: {exc}')


def _first_callable(obj: Any, names: list[str]) -> Callable[..., Any] | None:
    for name in names:
        value = getattr(obj, name, None)
        if callable(value):
            return value
    return None


def _as_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ('conversations', 'items', 'results', 'content'):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return [payload]
    return []


def _session_key_from_item(item: dict[str, Any]) -> str | None:
    for key in ('session_key', 'sessionKey', 'id'):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    return None
