from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import models, operator_models, tool_models, voice_models, webchat_models  # noqa: F401,E402
from app.db import db_context  # noqa: E402
from app.services.webcall_ai.config import get_webcall_ai_settings  # noqa: E402
from app.services.webcall_ai.worker import run_webcall_ai_worker_once  # noqa: E402


def main() -> int:
    settings = get_webcall_ai_settings()
    if not settings.room_presence_smoke_enabled:
        print("presence_smoke=disabled")
        return 2
    if not settings.room_presence_enabled:
        print("presence_smoke=presence_disabled")
        return 2
    with db_context() as db:
        result = run_webcall_ai_worker_once(
            db,
            worker_id="webcall-ai-presence-smoke",
            limit=1,
            lease_seconds=30,
            noop_release=True,
        )
    print(
        "presence_smoke=ok "
        f"claimed={int(result.get('claimed', 0))} "
        f"presence_joins={int(result.get('presence_joins', 0))} "
        f"presence_leaves={int(result.get('presence_leaves', 0))} "
        f"presence_failures={int(result.get('presence_failures', 0))} "
        f"failed={int(result.get('failed', 0))}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
