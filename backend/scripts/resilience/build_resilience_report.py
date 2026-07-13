#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from xml.etree import ElementTree

SCHEMA = "nexus_osr_resilience_qualification_v1"
_SHA_RE = re.compile(r"^[a-f0-9]{40}$")
_REQUIRED_TEST_NAMES = frozenset(
    {
        "test_concurrent_postgres_claims_never_duplicate_a_job",
        "test_concurrent_enqueue_keeps_one_active_dedupe_record",
        "test_expired_processing_lock_is_reclaimed_after_worker_crash",
    }
)


def _bounded_int(value: object) -> int:
    try:
        return max(0, min(int(float(str(value or 0))), 1_000_000))
    except (TypeError, ValueError, OverflowError):
        return 0


def _duration_ms(value: object) -> int:
    try:
        return max(0, min(round(float(str(value or 0)) * 1000), 3_600_000))
    except (TypeError, ValueError, OverflowError):
        return 0


def build_report(junit_path: Path, *, pytest_exit_code: int, source_sha: str) -> dict[str, object]:
    root = ElementTree.parse(junit_path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    tests = sum(_bounded_int(suite.attrib.get("tests")) for suite in suites)
    failures = sum(_bounded_int(suite.attrib.get("failures")) for suite in suites)
    errors = sum(_bounded_int(suite.attrib.get("errors")) for suite in suites)
    skipped = sum(_bounded_int(suite.attrib.get("skipped")) for suite in suites)
    duration_ms = sum(_duration_ms(suite.attrib.get("time")) for suite in suites)
    observed_test_names = {
        str(testcase.attrib.get("name") or "").strip()
        for testcase in root.iter("testcase")
    }
    observed_required = len(_REQUIRED_TEST_NAMES.intersection(observed_test_names))
    missing_required = len(_REQUIRED_TEST_NAMES) - observed_required
    normalized_sha = source_sha.strip().lower()
    if not _SHA_RE.fullmatch(normalized_sha):
        normalized_sha = "unknown"
    status = (
        "pass"
        if (
            pytest_exit_code == 0
            and tests >= len(_REQUIRED_TEST_NAMES)
            and failures == 0
            and errors == 0
            and skipped == 0
            and missing_required == 0
            and normalized_sha != "unknown"
        )
        else "fail"
    )
    return {
        "schema_version": SCHEMA,
        "status": status,
        "source_sha": normalized_sha,
        "database": "postgresql",
        "scenarios": {
            "concurrent_skip_locked_claim": "required",
            "concurrent_dedupe_enqueue": "required",
            "expired_worker_lock_recovery": "required",
        },
        "required_scenarios": {
            "expected": len(_REQUIRED_TEST_NAMES),
            "observed": observed_required,
            "missing": missing_required,
        },
        "counts": {
            "tests": tests,
            "failures": failures,
            "errors": errors,
            "skipped": skipped,
        },
        "duration_ms": min(duration_ms, 3_600_000),
        "external_effects": False,
        "production_data_used": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--junit", required=True)
    parser.add_argument("--pytest-exit-code", required=True, type=int)
    parser.add_argument("--output", required=True)
    parser.add_argument("--source-sha", default=os.getenv("GITHUB_SHA", "unknown"))
    args = parser.parse_args()

    report = build_report(
        Path(args.junit),
        pytest_exit_code=args.pytest_exit_code,
        source_sha=args.source_sha,
    )
    encoded = json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    if len(encoded.encode("utf-8")) > 8192:
        raise SystemExit("resilience_report_too_large")
    Path(args.output).write_text(encoded, encoding="utf-8")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
