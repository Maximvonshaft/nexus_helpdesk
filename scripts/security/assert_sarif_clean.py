#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

MAX_SARIF_BYTES = 64 * 1024 * 1024
MAX_RULES = 100


class SarifValidationError(ValueError):
    pass


def _sarif_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise SarifValidationError("sarif_input_missing")
    files = sorted(candidate for candidate in path.rglob("*.sarif") if candidate.is_file())
    if not files:
        raise SarifValidationError("sarif_files_missing")
    return files


def _load(path: Path) -> dict[str, Any]:
    size = path.stat().st_size
    if size <= 0 or size > MAX_SARIF_BYTES:
        raise SarifValidationError("sarif_size_invalid")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or str(payload.get("version") or "") != "2.1.0":
        raise SarifValidationError("sarif_schema_invalid")
    runs = payload.get("runs")
    if not isinstance(runs, list) or not runs:
        raise SarifValidationError("sarif_runs_missing")
    return payload


def evaluate(paths: Iterable[Path]) -> dict[str, Any]:
    result_count = 0
    by_rule: Counter[str] = Counter()
    by_level: Counter[str] = Counter()
    files = list(paths)
    for path in files:
        payload = _load(path)
        for run in payload["runs"]:
            if not isinstance(run, dict):
                raise SarifValidationError("sarif_run_invalid")
            results = run.get("results") or []
            if not isinstance(results, list):
                raise SarifValidationError("sarif_results_invalid")
            for result in results:
                if not isinstance(result, dict):
                    raise SarifValidationError("sarif_result_invalid")
                result_count += 1
                by_rule[str(result.get("ruleId") or "unknown")[:160]] += 1
                by_level[str(result.get("level") or "warning")[:32]] += 1
    return {
        "schema_version": "nexus_codeql_sarif_gate_v1",
        "status": "pass" if result_count == 0 else "fail",
        "sarif_file_count": len(files),
        "result_count": result_count,
        "by_level": dict(sorted(by_level.items())),
        "by_rule": [
            {"rule_id": rule, "count": count}
            for rule, count in sorted(by_rule.items())[:MAX_RULES]
        ],
        "rules_truncated": len(by_rule) > MAX_RULES,
        "contains_source_snippets": False,
        "contains_customer_data": False,
        "contains_secrets": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail closed when CodeQL SARIF contains findings.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        payload = evaluate(_sarif_files(args.input))
    except (OSError, UnicodeError, json.JSONDecodeError, SarifValidationError) as exc:
        payload = {
            "schema_version": "nexus_codeql_sarif_gate_v1",
            "status": "fail",
            "reason": str(exc)[:160],
            "contains_source_snippets": False,
            "contains_customer_data": False,
            "contains_secrets": False,
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": payload["status"], "result_count": payload.get("result_count")}, sort_keys=True))
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
