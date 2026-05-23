from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import SessionLocal  # noqa: E402
from app.services.webcall_ai.config import get_webcall_ai_settings  # noqa: E402
from app.services.webcall_ai.pilot_closure import run_webcall_ai_pilot_closure_once  # noqa: E402


def main() -> int:
    try:
        settings = get_webcall_ai_settings()
    except Exception as exc:
        print(f"pilot_closure=config_error error_code={type(exc).__name__}")
        return 1
    if not settings.pilot_closure_enabled:
        print("pilot_closure=disabled")
        return 2

    db = SessionLocal()
    try:
        result = run_webcall_ai_pilot_closure_once(db, worker_id="pilot-closure-smoke", mode=settings.pilot_mode)
    finally:
        db.close()
    if result.error_code == "no_session":
        print("pilot_closure=no_session")
        return 1
    if not result.ok:
        print(f"pilot_closure=failed mode={result.mode} error_code={result.error_code or 'unknown'}")
        return 1
    print(_format_result(result))
    return 0


def _format_result(result) -> str:
    fields = [
        "pilot_closure=ok",
        f"mode={result.mode}",
        f"claimed={result.claimed}",
        f"transcript_segments={result.transcript_segments}",
        f"turns={result.turns}",
        f"actions={result.actions}",
        f"tts_runtime_events={result.tts_runtime_events}",
        f"voice_egress_sent={result.voice_egress_sent}",
        f"handoff_events={result.handoff_events}",
        f"evidence_report={result.evidence_report}",
    ]
    return " ".join(fields)


if __name__ == "__main__":
    raise SystemExit(main())
