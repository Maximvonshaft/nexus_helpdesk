from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REQUIRED_TEST_NAMES = (
    "test_concurrent_postgres_claims_never_duplicate_a_job",
    "test_concurrent_enqueue_keeps_one_active_dedupe_record",
    "test_expired_processing_lock_is_reclaimed_after_worker_crash",
)


def _load_module():
    path = ROOT / "scripts" / "resilience" / "build_resilience_report.py"
    spec = importlib.util.spec_from_file_location("osr_resilience_report", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_junit(
    path: Path,
    *,
    names: tuple[str, ...] = REQUIRED_TEST_NAMES,
    failures: int = 0,
    errors: int = 0,
    skipped: int = 0,
) -> None:
    cases = "".join(
        f'<testcase classname="backend.tests.resilience" name="{name}" time="0.1" />'
        for name in names
    )
    path.write_text(
        (
            f'<testsuite tests="{len(names)}" failures="{failures}" errors="{errors}" '
            f'skipped="{skipped}" time="1.234">{cases}</testsuite>'
        ),
        encoding="utf-8",
    )


def test_report_contains_only_bounded_aggregate_evidence(tmp_path: Path) -> None:
    module = _load_module()
    junit = tmp_path / "junit.xml"
    _write_junit(junit)

    report = module.build_report(junit, pytest_exit_code=0, source_sha="a" * 40)

    assert report["status"] == "pass"
    assert report["counts"] == {"tests": 3, "failures": 0, "errors": 0, "skipped": 0}
    assert report["required_scenarios"] == {"expected": 3, "observed": 3, "missing": 0}
    assert report["external_effects"] is False
    assert report["production_data_used"] is False
    encoded = json.dumps(report, sort_keys=True)
    assert "backend.tests.resilience" not in encoded
    assert not any(name in encoded for name in REQUIRED_TEST_NAMES)
    assert len(encoded.encode("utf-8")) < 8192


def test_report_fails_closed_on_failed_or_incomplete_suite(tmp_path: Path) -> None:
    module = _load_module()
    junit = tmp_path / "junit.xml"
    _write_junit(junit, names=REQUIRED_TEST_NAMES[:2], failures=1)

    report = module.build_report(junit, pytest_exit_code=1, source_sha="not-a-sha")

    assert report["status"] == "fail"
    assert report["source_sha"] == "unknown"
    assert report["required_scenarios"]["missing"] == 1


def test_report_fails_closed_when_required_scenarios_are_skipped(tmp_path: Path) -> None:
    module = _load_module()
    junit = tmp_path / "junit.xml"
    _write_junit(junit, skipped=3)

    report = module.build_report(junit, pytest_exit_code=0, source_sha="b" * 40)

    assert report["status"] == "fail"
    assert report["counts"]["skipped"] == 3


def test_report_fails_closed_when_an_unrelated_test_replaces_a_required_scenario(tmp_path: Path) -> None:
    module = _load_module()
    junit = tmp_path / "junit.xml"
    _write_junit(junit, names=REQUIRED_TEST_NAMES[:2] + ("test_unrelated_green_check",))

    report = module.build_report(junit, pytest_exit_code=0, source_sha="c" * 40)

    assert report["status"] == "fail"
    assert report["counts"]["tests"] == 3
    assert report["required_scenarios"] == {"expected": 3, "observed": 2, "missing": 1}


def test_report_fails_closed_without_exact_source_identity(tmp_path: Path) -> None:
    module = _load_module()
    junit = tmp_path / "junit.xml"
    _write_junit(junit)

    report = module.build_report(junit, pytest_exit_code=0, source_sha="not-a-sha")

    assert report["status"] == "fail"
    assert report["source_sha"] == "unknown"
