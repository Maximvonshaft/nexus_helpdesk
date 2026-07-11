from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

SCHEMA_VERSION = "nexus_osr_integration_test_evidence_v1"
MAX_JUNIT_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_BYTES = 8 * 1024
MAX_COUNT = 1_000_000


@dataclass(frozen=True)
class IntegrationTestEvidence:
    status: str
    ready: bool
    evaluated_at: str
    reason_codes: tuple[str, ...]
    counts: dict[str, int]
    checks: dict[str, bool]
    source_sha256: str | None
    schema_version: str = SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "ready": self.ready,
            "evaluated_at": self.evaluated_at,
            "reason_codes": list(self.reason_codes),
            "counts": dict(self.counts),
            "checks": dict(self.checks),
            "source_sha256": self.source_sha256,
        }


def _as_count(value: Any) -> int:
    try:
        return max(0, min(int(value or 0), MAX_COUNT))
    except (TypeError, ValueError, OverflowError):
        raise ValueError("junit_count_invalid") from None


def _utc(value: datetime | None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _suite_counts(root: ElementTree.Element) -> dict[str, int]:
    if root.tag == "testsuite":
        suites = [root]
    elif root.tag == "testsuites":
        suites = list(root.findall("testsuite"))
    else:
        raise ValueError("junit_root_invalid")
    if not suites:
        raise ValueError("junit_suites_missing")
    return {
        name: sum(_as_count(suite.attrib.get(name, 0)) for suite in suites)
        for name in ("tests", "failures", "errors", "skipped")
    }


def build_test_evidence(path: Path, *, evaluated_at: datetime | None = None) -> IntegrationTestEvidence:
    now = _utc(evaluated_at)
    try:
        raw = path.read_bytes()
        if not raw or len(raw) > MAX_JUNIT_BYTES:
            raise ValueError("junit_size_invalid")
        root = ElementTree.fromstring(raw)
        counts = _suite_counts(root)
        executed = counts["tests"] > 0
        no_failures = counts["failures"] == 0
        no_errors = counts["errors"] == 0
        no_skips = counts["skipped"] == 0
        reasons: list[str] = []
        if not executed:
            reasons.append("integration_tests_missing")
        if not no_failures:
            reasons.append("integration_test_failures")
        if not no_errors:
            reasons.append("integration_test_errors")
        if not no_skips:
            reasons.append("integration_tests_skipped")
        ready = not reasons
        return IntegrationTestEvidence(
            status="ready" if ready else "not_ready",
            ready=ready,
            evaluated_at=now.isoformat(),
            reason_codes=tuple(reasons),
            counts=counts,
            checks={
                "tests_executed": executed,
                "no_failures": no_failures,
                "no_errors": no_errors,
                "no_skips": no_skips,
            },
            source_sha256=hashlib.sha256(raw).hexdigest(),
        )
    except (OSError, ElementTree.ParseError, RecursionError, TypeError, ValueError):
        return IntegrationTestEvidence(
            status="unavailable",
            ready=False,
            evaluated_at=now.isoformat(),
            reason_codes=("integration_test_evidence_unavailable",),
            counts={"tests": 0, "failures": 0, "errors": 0, "skipped": 0},
            checks={
                "tests_executed": False,
                "no_failures": False,
                "no_errors": False,
                "no_skips": False,
            },
            source_sha256=None,
        )


def encode_evidence(evidence: IntegrationTestEvidence) -> str:
    try:
        encoded = json.dumps(
            evidence.as_dict(),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        if len(encoded.encode("utf-8")) > MAX_OUTPUT_BYTES:
            raise ValueError("evidence_output_too_large")
        return encoded
    except (RecursionError, TypeError, ValueError, OverflowError):
        fallback = IntegrationTestEvidence(
            status="unavailable",
            ready=False,
            evaluated_at=datetime.now(timezone.utc).isoformat(),
            reason_codes=("integration_test_evidence_unavailable",),
            counts={"tests": 0, "failures": 0, "errors": 0, "skipped": 0},
            checks={"tests_executed": False, "no_failures": False, "no_errors": False, "no_skips": False},
            source_sha256=None,
        )
        return json.dumps(fallback.as_dict(), ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def exit_code(status: str) -> int:
    if status == "ready":
        return 0
    if status == "unavailable":
        return 2
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build bounded OSR integration evidence from a pytest JUnit report.")
    parser.add_argument("--junit", type=Path, required=True)
    args = parser.parse_args(argv)
    evidence = build_test_evidence(args.junit)
    print(encode_evidence(evidence))
    return exit_code(evidence.status)


if __name__ == "__main__":
    raise SystemExit(main())
