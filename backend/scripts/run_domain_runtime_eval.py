from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.domain_intelligence import understand_query  # noqa: E402


def _load_cases(paths: list[Path]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("cases"), list):
            cases.extend(data["cases"])
        elif isinstance(data, list):
            cases.extend(data)
        else:
            raise ValueError(f"unsupported fixture shape: {path}")
    return cases


def run_eval(paths: list[Path]) -> dict[str, Any]:
    cases = _load_cases(paths)
    results = []
    passed = 0
    for case in cases:
        query = str(case.get("query") or "")
        expected = case.get("expected_primary_intent")
        result = understand_query(query)
        ok = expected is None or result.primary_intent == expected
        passed += int(ok)
        results.append({
            "id": case.get("id"),
            "query": query,
            "expected_primary_intent": expected,
            "actual_primary_intent": result.primary_intent,
            "ok": ok,
            "trace": result.as_trace(),
        })
    return {"total": len(results), "passed": passed, "failed": len(results) - passed, "results": results}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run domain runtime evaluation fixtures.")
    parser.add_argument("--fixture", action="append", default=[], help="Path to a JSON fixture file.")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero on failed cases.")
    args = parser.parse_args()
    paths = [Path(item).resolve() for item in args.fixture]
    if not paths:
        raise SystemExit("at least one --fixture is required")
    report = run_eval(paths)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.strict and report["failed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
