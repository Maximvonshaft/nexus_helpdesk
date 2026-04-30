from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.openclaw_runtime_service import probe_openclaw_connectivity  # noqa: E402


def main() -> int:
    result = probe_openclaw_connectivity()
    payload = result.model_dump(mode='json') if hasattr(result, 'model_dump') else dict(result)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    warnings = payload.get('warnings') or []
    level = payload.get('level') or 'L0'
    return 0 if level in {'L2', 'L3', 'L4', 'L5'} and not warnings else 2


if __name__ == '__main__':
    raise SystemExit(main())
