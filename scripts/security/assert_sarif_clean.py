#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote

MAX_SARIF_BYTES = 64 * 1024 * 1024
MAX_RULES = 100
MAX_EXCEPTIONS = 20


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


def _safe_relative_path(value: object) -> str:
    text = unquote(str(value or "")).replace("\\\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    candidate = Path(text)
    if not text or candidate.is_absolute() or ".." in candidate.parts:
        raise SarifValidationError("exception_path_invalid")
    return candidate.as_posix()


def _result_location(result: dict[str, Any]) -> tuple[str, int] | None:
    locations = result.get("locations")
    if not isinstance(locations, list) or not locations:
        return None
    physical = locations[0].get("physicalLocation") if isinstance(locations[0], dict) else None
    if not isinstance(physical, dict):
        return None
    artifact = physical.get("artifactLocation")
    region = physical.get("region")
    if not isinstance(artifact, dict) or not isinstance(region, dict):
        return None
    try:
        path = _safe_relative_path(artifact.get("uri"))
        line = int(region.get("startLine"))
    except (TypeError, ValueError, SarifValidationError):
        return None
    return path, line


def _load_exceptions(path: Path | None, *, root: Path) -> dict[tuple[str, str, int], dict[str, Any]]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != "nexus_codeql_exception_policy_v1":
        raise SarifValidationError("exception_schema_invalid")
    entries = payload.get("exceptions")
    if not isinstance(entries, list) or len(entries) > MAX_EXCEPTIONS:
        raise SarifValidationError("exception_entries_invalid")
    result: dict[tuple[str, str, int], dict[str, Any]] = {}
    today = date.today()
    for entry in entries:
        if not isinstance(entry, dict):
            raise SarifValidationError("exception_entry_invalid")
        rule_id = str(entry.get("rule_id") or "").strip()
        source_path = _safe_relative_path(entry.get("path"))
        try:
            start_line = int(entry.get("start_line"))
            expires_on = date.fromisoformat(str(entry.get("expires_on") or ""))
        except (TypeError, ValueError) as exc:
            raise SarifValidationError("exception_metadata_invalid") from exc
        owner = str(entry.get("owner") or "").strip()
        reason = " ".join(str(entry.get("reason") or "").split())
        markers = entry.get("required_markers")
        if (
            not rule_id
            or start_line < 1
            or expires_on <= today
            or not owner
            or not 20 <= len(reason) <= 500
            or not isinstance(markers, list)
            or not markers
            or any(not isinstance(marker, str) or not marker.strip() for marker in markers)
        ):
            raise SarifValidationError("exception_metadata_invalid")
        source_file = root / source_path
        if not source_file.is_file():
            raise SarifValidationError("exception_source_missing")
        source = source_file.read_text(encoding="utf-8")
        if any(marker not in source for marker in markers):
            raise SarifValidationError("exception_source_marker_missing")
        key = (rule_id, source_path, start_line)
        if key in result:
            raise SarifValidationError("exception_duplicate")
        result[key] = {
            "rule_id": rule_id,
            "path": source_path,
            "start_line": start_line,
            "owner": owner,
            "expires_on": expires_on.isoformat(),
            "reason": reason,
        }
    return result


def evaluate(
    paths: Iterable[Path],
    *,
    exceptions: dict[tuple[str, str, int], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    result_count = 0
    raw_result_count = 0
    approved_exception_count = 0
    by_rule: Counter[str] = Counter()
    by_level: Counter[str] = Counter()
    approved_by_rule: Counter[str] = Counter()
    files = list(paths)
    exception_map = exceptions or {}
    used_exceptions: set[tuple[str, str, int]] = set()
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
                raw_result_count += 1
                rule_id = str(result.get("ruleId") or "unknown")[:160]
                location = _result_location(result)
                key = (rule_id, *location) if location is not None else None
                if key is not None and key in exception_map:
                    approved_exception_count += 1
                    approved_by_rule[rule_id] += 1
                    used_exceptions.add(key)
                    continue
                result_count += 1
                by_rule[rule_id] += 1
                by_level[str(result.get("level") or "warning")[:32]] += 1
    unused = sorted(set(exception_map) - used_exceptions)
    status = "pass" if result_count == 0 and not unused else "fail"
    return {
        "schema_version": "nexus_codeql_sarif_gate_v2",
        "status": status,
        "sarif_file_count": len(files),
        "raw_result_count": raw_result_count,
        "result_count": result_count,
        "approved_exception_count": approved_exception_count,
        "unused_exception_count": len(unused),
        "unused_exceptions": [
            {"rule_id": rule, "path": source_path, "start_line": line}
            for rule, source_path, line in unused[:MAX_RULES]
        ],
        "by_level": dict(sorted(by_level.items())),
        "by_rule": [
            {"rule_id": rule, "count": count}
            for rule, count in sorted(by_rule.items())[:MAX_RULES]
        ],
        "approved_by_rule": [
            {"rule_id": rule, "count": count}
            for rule, count in sorted(approved_by_rule.items())[:MAX_RULES]
        ],
        "rules_truncated": len(by_rule) > MAX_RULES,
        "contains_source_snippets": False,
        "contains_customer_data": False,
        "contains_secrets": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail closed when CodeQL SARIF contains unapproved findings.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--exceptions", type=Path)
    args = parser.parse_args()
    try:
        exceptions = _load_exceptions(args.exceptions, root=Path.cwd())
        payload = evaluate(_sarif_files(args.input), exceptions=exceptions)
    except (OSError, UnicodeError, json.JSONDecodeError, SarifValidationError) as exc:
        payload = {
            "schema_version": "nexus_codeql_sarif_gate_v2",
            "status": "fail",
            "reason": str(exc)[:160],
            "contains_source_snippets": False,
            "contains_customer_data": False,
            "contains_secrets": False,
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": payload["status"],
                "result_count": payload.get("result_count"),
                "approved_exception_count": payload.get("approved_exception_count"),
            },
            sort_keys=True,
        )
    )
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
