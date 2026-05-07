from __future__ import annotations

import os
import re
from time import perf_counter
from typing import Any

import httpx

from ..settings import get_settings
from .observability import LOGGER, record_openclaw_bridge_metric
from .openclaw_mcp_client import OpenClawMCPClient

settings = get_settings()

_SECRET_RE = re.compile(r"(?i)(token|secret|password|api[_-]?key)=([^&\s]+)")


class OpenClawBridgeHTTPError(RuntimeError):
    pass


def _bridge_timeout() -> httpx.Timeout:
    total = max(float(settings.openclaw_bridge_timeout_seconds), 1.0)
    short = min(3.0, total)
    return httpx.Timeout(timeout=total, connect=short, read=total, write=short, pool=short)


def _bridge_limits() -> httpx.Limits:
    max_connections = int(os.getenv("OPENCLAW_BRIDGE_MAX_CONNECTIONS", "20"))
    max_keepalive = int(os.getenv("OPENCLAW_BRIDGE_MAX_KEEPALIVE_CONNECTIONS", "10"))
    keepalive_expiry = float(os.getenv("OPENCLAW_BRIDGE_KEEPALIVE_EXPIRY_SECONDS", "30"))
    return httpx.Limits(
        max_connections=max_connections,
        max_keepalive_connections=max_keepalive,
        keepalive_expiry=keepalive_expiry,
    )


def _safe_error_text(exc: BaseException | str) -> str:
    text = str(exc)
    text = _SECRET_RE.sub(lambda m: f"{m.group(1)}=<redacted>", text)
    # Avoid logging full bridge URLs or arbitrary response bodies. Keep only a
    # bounded, sanitized diagnostic string.
    return text.replace(settings.openclaw_bridge_url, "<bridge_url>")[:240]


class OpenClawBridgeHTTPClient:
    """Pooled HTTP client for NexusDesk -> OpenClaw Bridge remote_gateway mode.

    This client deliberately exposes only read/sync primitives used by transcript
    sync. It has no send_message method and never starts a local OpenClaw CLI
    subprocess.
    """

    def __init__(
        self,
        *,
        bridge_url: str | None = None,
        timeout_seconds: int | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.bridge_url = (bridge_url or settings.openclaw_bridge_url).rstrip('/')
        self.timeout_seconds = timeout_seconds or settings.openclaw_bridge_timeout_seconds
        self._external_client = client is not None
        self.client = client or httpx.Client(
            base_url=self.bridge_url,
            timeout=_bridge_timeout(),
            limits=_bridge_limits(),
            headers={
                'Content-Type': 'application/json',
                'User-Agent': 'nexusdesk-openclaw-bridge/1.0',
            },
        )

    def __enter__(self) -> "OpenClawBridgeHTTPClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if not self._external_client:
            self.client.close()

    def close(self) -> None:
        if not self._external_client:
            self.client.close()

    def _record(self, operation: str, status: str, started: float, *, error: BaseException | str | None = None) -> None:
        elapsed_ms = (perf_counter() - started) * 1000.0
        record_openclaw_bridge_metric(operation, status, elapsed_ms)
        if error is not None:
            LOGGER.warning(
                'openclaw_bridge_http_degraded',
                extra={'event_payload': {
                    'operation': operation,
                    'status': status,
                    'elapsed_ms': round(elapsed_ms, 2),
                    'timeout_seconds': self.timeout_seconds,
                    'error': _safe_error_text(error),
                }},
            )

    def _post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        endpoint_path = f"/{endpoint.lstrip('/')}"
        operation = endpoint_path.strip('/').replace('-', '_') or 'unknown'
        started = perf_counter()
        try:
            response = self.client.post(endpoint_path, json=payload)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            self._record(operation, 'timeout', started, error=exc)
            raise OpenClawBridgeHTTPError('bridge_timeout') from exc
        except httpx.HTTPStatusError as exc:
            self._record(operation, f'http_{exc.response.status_code}', started, error=exc)
            raise OpenClawBridgeHTTPError(f'bridge_http_{exc.response.status_code}') from exc
        except httpx.HTTPError as exc:
            self._record(operation, 'transport_error', started, error=exc)
            raise OpenClawBridgeHTTPError('bridge_transport_error') from exc

        try:
            data = response.json()
        except ValueError as exc:
            self._record(operation, 'invalid_json', started, error=exc)
            raise OpenClawBridgeHTTPError('bridge_invalid_json') from exc
        if not isinstance(data, dict):
            self._record(operation, 'invalid_payload', started, error='non_object_payload')
            raise OpenClawBridgeHTTPError('bridge_invalid_payload')
        if data.get('ok') is False:
            self._record(operation, 'bridge_error', started, error=str(data.get('error') or 'bridge_error'))
            raise OpenClawBridgeHTTPError('bridge_error')
        self._record(operation, 'success', started)
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
