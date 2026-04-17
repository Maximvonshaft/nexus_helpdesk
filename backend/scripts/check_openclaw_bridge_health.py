from __future__ import annotations

import json
import os
import sys
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

bridge_url = (os.getenv('OPENCLAW_BRIDGE_URL', 'http://127.0.0.1:18792').strip() or 'http://127.0.0.1:18792').rstrip('/')


def main() -> int:
    request = urllib.request.Request(f'{bridge_url}/health', method='GET')
    with urllib.request.urlopen(request, timeout=10) as resp:
        payload = json.loads(resp.read().decode('utf-8'))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if payload.get('ok') and payload.get('gateway', {}).get('connected'):
        return 0
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
