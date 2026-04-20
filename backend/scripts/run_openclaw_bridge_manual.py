from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)

env_file = ROOT / '.env.local-manual'
if env_file.exists():
    for raw in env_file.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        os.environ.setdefault(key, value)

node_bin = os.getenv('NODE_BIN', 'node')
script_path = ROOT / 'scripts' / 'openclaw_bridge_server.js'

os.execvp(node_bin, [node_bin, str(script_path)])
