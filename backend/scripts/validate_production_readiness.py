from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.readiness_service import evaluate_production_readiness  # noqa: E402


def main() -> int:
    payload = evaluate_production_readiness(db=None)
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0 if not payload.get('warnings') and not payload.get('failures') else 2


if __name__ == '__main__':
    raise SystemExit(main())
