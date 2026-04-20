from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent
DEPS = PROJECT / '.pydeps'
os.chdir(ROOT)
if str(DEPS) not in sys.path:
    sys.path.insert(0, str(DEPS))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

env_file = ROOT / '.env.local-manual'
if env_file.exists():
    for raw in env_file.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        os.environ.setdefault(key, value)

from uvicorn import run

port = int(os.getenv("PORT", 8888))
run('app.main:app', host='0.0.0.0', port=port)
