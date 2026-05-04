from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
env_file = ROOT / '.env.local-manual'
if env_file.exists():
    for raw in env_file.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        os.environ.setdefault(key, value)

bridge_url = (os.getenv('OPENCLAW_BRIDGE_URL', 'http://127.0.0.1:18792').strip() or 'http://127.0.0.1:18792').rstrip('/')


def _truthy(raw: str | None, *, default: bool = False) -> bool:
    if raw is None or raw == '':
        return default
    return raw.strip().lower() in {'1', 'true', 'yes', 'on'}


def _payload_bool(payload: dict[str, Any], key: str, *, env_name: str, default: bool) -> bool:
    value = payload.get(key)
    if isinstance(value, bool):
        return value
    return _truthy(os.getenv(env_name), default=default)


def _payload_text(payload: dict[str, Any], key: str, *, env_name: str, default: str) -> str:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return (os.getenv(env_name, default).strip() or default)


def _csv(raw: str | None) -> list[str]:
    return [item.strip() for item in (raw or '').split(',') if item.strip()]


def _runtime_module_exists(payload: dict[str, Any]) -> bool | None:
    module_path = payload.get('runtime', {}).get('gatewayRuntimeModule')
    if not module_path:
        return None
    try:
        return Path(str(module_path)).exists()
    except OSError:
        return False


def _diagnose(payload: dict[str, Any] | None, *, http_ok: bool, error: str | None = None) -> dict[str, Any]:
    payload = payload or {}
    gateway = payload.get('gateway') if isinstance(payload.get('gateway'), dict) else {}
    allow_writes = _payload_bool(
        payload,
        'allowWrites',
        env_name='OPENCLAW_BRIDGE_ALLOW_WRITES',
        default=False,
    )
    send_message_enabled = _payload_bool(
        payload,
        'sendMessageEnabled',
        env_name='OPENCLAW_BRIDGE_ALLOW_WRITES',
        default=allow_writes,
    )
    ai_reply_enabled = _payload_bool(
        payload,
        'aiReplyEnabled',
        env_name='OPENCLAW_BRIDGE_AI_REPLY_ENABLED',
        default=True,
    )
    tracking_lookup_enabled = _payload_bool(
        payload,
        'trackingLookupEnabled',
        env_name='OPENCLAW_BRIDGE_TRACKING_LOOKUP_ENABLED',
        default=False,
    )
    tracking_lookup_method = _payload_text(
        payload,
        'trackingLookupMethod',
        env_name='OPENCLAW_BRIDGE_TRACKING_LOOKUP_METHOD',
        default='tools.call',
    )
    tracking_lookup_tool_name = _payload_text(
        payload,
        'trackingLookupToolName',
        env_name='OPENCLAW_BRIDGE_TRACKING_LOOKUP_TOOL_NAME',
        default='speedaf-support__speedaf_lookup',
    )
    scopes = _csv(os.getenv('OPENCLAW_BRIDGE_GATEWAY_SCOPES', 'operator.read'))
    gateway_connected = bool(gateway.get('connected'))
    summary = {
        'ok': bool(http_ok and gateway_connected),
        'bridge_http_ok': http_ok,
        'gateway_connected': gateway_connected,
        'bridge_url': bridge_url,
        'allow_writes': allow_writes,
        'send_message_enabled': send_message_enabled,
        'ai_reply_enabled': ai_reply_enabled,
        'tracking_lookup_enabled': tracking_lookup_enabled,
        'tracking_lookup_method': tracking_lookup_method,
        'tracking_lookup_tool_name': tracking_lookup_tool_name,
        'gateway_scopes': scopes,
        'write_scope_present': 'operator.write' in scopes,
        'runtime_module_exists': _runtime_module_exists(payload),
        'last_connect_error': gateway.get('lastConnectError'),
        'last_close': gateway.get('lastClose'),
        'error': error,
    }
    if http_ok and not gateway_connected:
        summary['diagnosis'] = 'bridge_http_ok_but_gateway_disconnected'
    elif not http_ok:
        summary['diagnosis'] = 'bridge_http_unreachable'
    elif send_message_enabled and 'operator.write' not in scopes:
        summary['diagnosis'] = 'bridge_send_message_enabled_without_operator_write_scope'
        summary['ok'] = False
    else:
        summary['diagnosis'] = 'ok'
    return summary


def main() -> int:
    try:
        request = urllib.request.Request(f'{bridge_url}/health', method='GET')
        with urllib.request.urlopen(request, timeout=10) as resp:
            payload = json.loads(resp.read().decode('utf-8'))
        summary = _diagnose(payload, http_ok=True)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        summary = _diagnose(None, http_ok=False, error=str(exc))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())
