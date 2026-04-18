from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.openclaw_runtime_service import probe_openclaw_connectivity  # noqa: E402


def main() -> int:
    result = probe_openclaw_connectivity()
    print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2, default=str))
    if result.bridge_started and result.conversations_tool_ok:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
