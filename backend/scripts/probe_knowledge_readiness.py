from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db import SessionLocal  # noqa: E402
from app.services.knowledge_readiness_service import (  # noqa: E402
    build_knowledge_readiness,
    unavailable_report,
)
from app.settings import get_settings  # noqa: E402


def exit_code_for_status(status: str, *, allow_degraded: bool = False) -> int:
    normalized = (status or "unavailable").strip().lower()
    if normalized == "ready":
        return 0
    if normalized == "degraded":
        return 0 if allow_degraded else 1
    if normalized == "unavailable":
        return 2
    return 1


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1 or parsed > 3650:
        raise argparse.ArgumentTypeError("freshness days must be between 1 and 3650")
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate Nexus customer-visible Knowledge readiness.")
    parser.add_argument("--tenant", default=os.getenv("KNOWLEDGE_READINESS_TENANT") or None)
    parser.add_argument("--country", default=os.getenv("KNOWLEDGE_READINESS_COUNTRY") or None)
    parser.add_argument("--channel", default=os.getenv("KNOWLEDGE_READINESS_CHANNEL") or None)
    parser.add_argument(
        "--freshness-days",
        type=_positive_int,
        default=_positive_int(os.getenv("KNOWLEDGE_READINESS_FRESHNESS_DAYS", "90")),
    )
    parser.add_argument("--allow-degraded", action="store_true")
    args = parser.parse_args()

    db = None
    try:
        db = SessionLocal()
        report = build_knowledge_readiness(
            db,
            settings=get_settings(),
            expected_tenant=args.tenant,
            expected_country=args.country,
            expected_channel=args.channel,
            freshness_days=args.freshness_days,
        )
    except Exception:
        report = unavailable_report()
    finally:
        if db is not None:
            close = getattr(db, "close", None)
            if callable(close):
                close()

    payload = report.as_admin_read_model()
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return exit_code_for_status(report.status, allow_degraded=args.allow_degraded)


if __name__ == "__main__":
    raise SystemExit(main())
