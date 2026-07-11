from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db import SessionLocal  # noqa: E402
from app.services.knowledge_readiness_service import (  # noqa: E402
    KnowledgeReadinessReport,
    build_knowledge_readiness,
    unavailable_report,
)
from app.settings import get_settings  # noqa: E402

MAX_PROBE_BYTES = 32_768


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
    if parsed < 1 or parsed > 3_650:
        raise argparse.ArgumentTypeError("freshness days must be between 1 and 3650")
    return parsed


def encode_report(report: KnowledgeReadinessReport) -> str:
    """Return one bounded, deterministic JSON object or a fixed unavailable report."""
    try:
        encoded = json.dumps(
            report.as_admin_read_model(),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        parsed: Any = json.loads(encoded)
        if not isinstance(parsed, dict) or len(encoded.encode("utf-8")) > MAX_PROBE_BYTES:
            raise ValueError("invalid_probe_contract")
        return encoded
    except (RecursionError, TypeError, ValueError, OverflowError):
        fallback = unavailable_report().as_admin_read_model()
        return json.dumps(fallback, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate customer-visible Knowledge readiness without external calls.")
    parser.add_argument("--tenant", default=os.getenv("KNOWLEDGE_READINESS_TENANT") or None)
    parser.add_argument("--brand", default=os.getenv("KNOWLEDGE_READINESS_BRAND") or None)
    parser.add_argument("--country", default=os.getenv("KNOWLEDGE_READINESS_COUNTRY") or None)
    parser.add_argument("--channel", default=os.getenv("KNOWLEDGE_READINESS_CHANNEL") or None)
    parser.add_argument("--audience", default=os.getenv("KNOWLEDGE_READINESS_AUDIENCE") or "customer")
    parser.add_argument(
        "--freshness-days",
        type=_positive_int,
        default=_positive_int(os.getenv("KNOWLEDGE_READINESS_FRESHNESS_DAYS", "90")),
    )
    parser.add_argument("--allow-degraded", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    db = None
    try:
        db = SessionLocal()
        report = build_knowledge_readiness(
            db,
            settings=get_settings(),
            expected_tenant=args.tenant,
            expected_brand=args.brand,
            expected_country=args.country,
            expected_channel=args.channel,
            expected_audience=args.audience,
            freshness_days=args.freshness_days,
        )
    except Exception:
        report = unavailable_report()
    finally:
        if db is not None:
            close = getattr(db, "close", None)
            if callable(close):
                close()

    encoded = encode_report(report)
    print(encoded)
    encoded_status = str(json.loads(encoded).get("status") or "unavailable")
    return exit_code_for_status(encoded_status, allow_degraded=args.allow_degraded)


if __name__ == "__main__":
    raise SystemExit(main())
