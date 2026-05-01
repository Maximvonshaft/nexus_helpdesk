from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
env_file = ROOT / '.env.local-manual'
if env_file.exists():
    for raw in env_file.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        os.environ.setdefault(key, value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('--timeout', type=float, default=20.0)
    parser.add_argument('--interval', type=float, default=1.0)
    return parser.parse_args()


def bridge_url() -> str:
    return (os.getenv('OPENCLAW_BRIDGE_URL', 'http://127.0.0.1:18792').strip() or 'http://127.0.0.1:18792').rstrip('/')


def bridge_enabled() -> bool:
    return os.getenv('OPENCLAW_BRIDGE_ENABLED', 'false').strip().lower() == 'true'


def main() -> int:
    args = parse_args()
    if not bridge_enabled():
        print(json.dumps({'ok': True, 'skipped': True, 'reason': 'OPENCLAW_BRIDGE_ENABLED is false'}))
        return 0

    deadline = time.monotonic() + max(args.timeout, 0.0)
    last_error = None
    health_url = f'{bridge_url()}/health'

    while time.monotonic() <= deadline:
        try:
            with urllib.request.urlopen(urllib.request.Request(health_url, method='GET'), timeout=5) as resp:
                payload = json.loads(resp.read().decode('utf-8'))
            if payload.get('ok') and payload.get('gateway', {}).get('connected'):
                print(json.dumps({'ok': True, 'health_url': health_url, 'diagnosis': 'ok'}, ensure_ascii=False))
                return 0
            last_error = 'gateway_disconnected'
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            last_error = f'bridge_http_unreachable: {exc}'
        time.sleep(max(args.interval, 0.1))

    print(json.dumps({'ok': False, 'health_url': health_url, 'diagnosis': last_error}, ensure_ascii=False))
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
