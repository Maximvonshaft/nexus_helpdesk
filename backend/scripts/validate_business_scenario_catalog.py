from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.nexus_osr.business_scenarios import (  # noqa: E402
    BusinessScenarioCatalogError,
    load_business_scenario_catalog,
)


def _parse_at(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("--at must include a timezone")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the bounded Nexus OSR business-scenario catalog without emitting scenario bodies."
    )
    parser.add_argument("--path", type=Path, default=None, help="Optional catalog path; defaults to the production catalog.")
    parser.add_argument("--at", type=_parse_at, default=None, help="ISO-8601 evaluation timestamp.")
    parser.add_argument(
        "--allow-inactive",
        action="store_true",
        help="Validate schema/lifecycle syntax without requiring every scenario to be currently active.",
    )
    args = parser.parse_args(argv)

    try:
        catalog = load_business_scenario_catalog(
            args.path,
            at=args.at,
            require_all_active=not args.allow_inactive,
        )
    except BusinessScenarioCatalogError as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "schema": "nexus.business-scenario-validation.v1",
                    "reason": exc.reason,
                },
                sort_keys=True,
            )
        )
        return 2

    print(
        json.dumps(
            {
                "ok": True,
                "schema": "nexus.business-scenario-validation.v1",
                "catalog": catalog.safe_summary(),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
