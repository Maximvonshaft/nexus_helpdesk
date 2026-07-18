#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from fastapi.routing import APIRoute  # noqa: E402

_PATH_PARAMETER = re.compile(r"\{[^{}]+\}")


def normalized_path(path: str) -> str:
    """Normalize parameter names/converters so aliases cannot hide collisions."""

    value = "/" + "/".join(part for part in str(path).strip().split("/") if part)
    return _PATH_PARAMETER.sub("{}", value or "/")


def route_inventory(app: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        endpoint = route.endpoint
        source_module = getattr(endpoint, "__module__", None)
        source_name = getattr(endpoint, "__qualname__", getattr(endpoint, "__name__", route.name))
        for method in sorted(route.methods or ()):
            records.append(
                {
                    "method": method.upper(),
                    "path": route.path,
                    "normalized_path": normalized_path(route.path),
                    "name": route.name,
                    "source_module": source_module,
                    "source_name": source_name,
                    "deprecated": bool(route.deprecated),
                    "include_in_schema": bool(route.include_in_schema),
                }
            )
    return sorted(records, key=lambda row: (row["normalized_path"], row["method"], row["path"]))


def duplicate_routes(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[(record["method"], record["normalized_path"])].append(record)
    return [
        {
            "method": method,
            "normalized_path": path,
            "registrations": registrations,
        }
        for (method, path), registrations in sorted(grouped.items())
        if len(registrations) > 1
    ]


def qualification_payload(app: Any) -> dict[str, Any]:
    routes = route_inventory(app)
    duplicates = duplicate_routes(routes)
    return {
        "schema": "nexus.fastapi-route-authority.v1",
        "status": "pass" if not duplicates else "fail",
        "route_count": len(routes),
        "duplicates": duplicates,
        "routes": routes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate and validate the canonical FastAPI route table.")
    parser.add_argument("--out", type=Path, help="Optional JSON evidence output path.")
    args = parser.parse_args()

    from app.main import app

    payload = qualification_payload(app)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
