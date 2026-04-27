from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from ..settings import get_settings
from .openclaw_mcp_client import OpenClawMCPClient

settings = get_settings()


class OpenClawBridgeHTTPError(RuntimeError):
    pass


class OpenClawBridgeHTTPClient:
    """Read-only HTTP client for NexusDesk -> OpenClaw Bridge remote_gateway mode.

    This client deliberately exposes only read/sync primitives used by transcript
    sync. It has no send_message method and never starts a local OpenClaw CLI
    subprocess.
    """

    def __init__(self, *, bridge_url: str | None = None, timeout_seconds: int | None = None) -> None:
        self.bridge_url = (bridge_url or settings.openclaw_bridge_url).rstrip('/')
        self.timeout_seconds = timeout_seconds or settings.openclaw_bridge_timeout_seconds

    def __enter__(self) -> "OpenClawBridgeHTTPClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def _post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.bridge_url}/{endpoint.lstrip('/')}"
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        request = urllib.request.Request(
            url,
            data=body,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as resp:
                raw = resp.read().decode('utf-8')
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode('utf-8', errors='replace') if exc.fp else ''
            raise OpenClawBridgeHTTPError(f'bridge_http_{exc.code}: {detail or exc.reason}') from exc
        except Exception as exc:
            raise OpenClawBridgeHTTPError(f'bridge_http_failed: {exc}') from exc
        try:
            data = json.loads(raw or '{}')
        except json.JSONDecodeError as exc:
            raise OpenClawBridgeHTTPError(f'bridge_invalid_json: {raw[:200]}') from exc
        if not isinstance(data, dict):
            raise OpenClawBridgeHTTPError('bridge_invalid_payload')
        if data.get('ok') is False:
            raise OpenClawBridgeHTTPError(str(data.get('error') or 'bridge_error'))
        return data

    def conversations_list(self, *, limit: int = 50, agent: str = 'support') -> dict[str, Any]:
        return self._post('/conversations-list', {'limit': limit, 'agent': agent})

    def conversation_get(self, session_key: str) -> dict[str, Any] | None:
        data = self._post('/conversation-get', {'sessionKey': session_key})
        return data.get('conversation') if isinstance(data.get('conversation'), dict) else data

    def messages_read(self, session_key: str, *, limit: int = 50) -> list[dict[str, Any]]:
        data = self._post('/read-messages', {'sessionKey': session_key, 'limit': limit})
        messages = data.get('messages')
        return [item for item in messages if isinstance(item, dict)] if isinstance(messages, list) else []

    def attachments_fetch(self, message_id: str, *, session_key: str | None = None) -> list[dict[str, Any]]:
        # sync_openclaw_conversation already tries the bridge-native /attachments-fetch
        # path with sessionKey. This MCP-compatible fallback signature does not receive
        # sessionKey, so returning an empty list is safer than any local CLI fallback.
        if not session_key:
            return []
        data = self._post('/attachments-fetch', {'sessionKey': session_key, 'messageId': message_id})
        attachments = data.get('attachments')
        return [item for item in attachments if isinstance(item, dict)] if isinstance(attachments, list) else []


def is_remote_bridge_mode() -> bool:
    return settings.openclaw_deployment_mode == 'remote_gateway' and settings.openclaw_bridge_enabled


def get_openclaw_runtime_client():
    if is_remote_bridge_mode():
        return OpenClawBridgeHTTPClient()
    return OpenClawMCPClient()
