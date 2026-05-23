from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import models, operator_models, tool_models, voice_models, webchat_fast_models, webchat_models  # noqa: F401,E402
from app.db import db_context  # noqa: E402
from app.services.observability import configure_logging  # noqa: E402
from app.services.webcall_ai.worker import run_webcall_ai_worker_once  # noqa: E402
from app.settings import get_settings  # noqa: E402

settings = get_settings()
configure_logging(settings.log_json)


def run_once(worker_id: str, *, limit: int, lease_seconds: int) -> dict[str, int]:
    with db_context() as db:
        return run_webcall_ai_worker_once(
            db,
            worker_id=worker_id,
            limit=limit,
            lease_seconds=lease_seconds,
            noop_release=True,
        )


def _format_result(result: dict[str, int]) -> str:
    formatted = (
        f"claimed={int(result.get('claimed', 0))} "
        f"turns={int(result.get('turns', 0))} "
        f"stt_events={int(result.get('stt_events', 0))} "
        f"tts_events={int(result.get('tts_events', 0))} "
        f"released={int(result.get('released', 0))} "
        f"failed={int(result.get('failed', 0))} "
        f"skipped={int(result.get('skipped', 0))}"
    )
    if "participants" in result:
        formatted = (
            f"{formatted} "
            f"participants={int(result.get('participants', 0))} "
            f"participant_joins={int(result.get('participant_joins', 0))} "
            f"participant_leaves={int(result.get('participant_leaves', 0))}"
        )
    return formatted


def main() -> int:
    parser = argparse.ArgumentParser(description="Run WebCall AI worker claim lifecycle skeleton")
    parser.add_argument("--worker-id", default="webcall-ai-worker-main")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--lease-seconds", type=int, default=30)
    args = parser.parse_args()

    while True:
        result = run_once(args.worker_id, limit=args.limit, lease_seconds=args.lease_seconds)
        if args.once:
            print(_format_result(result))
            return 0
        time.sleep(settings.worker_poll_seconds if int(result.get("claimed", 0)) == 0 else 0.2)


if __name__ == "__main__":
    raise SystemExit(main())
