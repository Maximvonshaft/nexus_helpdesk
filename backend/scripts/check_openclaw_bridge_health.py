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


def _truthy(raw: str | None) -> bool:
    return (raw or '').strip().lower() in {'1', 'true', 'yes', 'on'}


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
    allow_writes = _truthy(os.getenv('OPENCLAW_BRIDGE_ALLOW_WRITES', 'false'))
    scopes = _csv(os.getenv('OPENCLAW_BRIDGE_GATEWAY_SCOPES', 'operator.read'))
    gateway_connected = bool(gateway.get('connected'))
    summary = {
        'ok': bool(http_ok and gateway_connected),
        'bridge_http_ok': http_ok,
        'gateway_connected': gateway_connected,
        'bridge_url': bridge_url,
        'allow_writes': allow_writes,
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
    elif allow_writes and 'operator.write' not in scopes:
        summary['diagnosis'] = 'bridge_write_enabled_without_operator_write_scope'
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
