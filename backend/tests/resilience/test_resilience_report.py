from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_module():
    path = ROOT / "scripts" / "resilience" / "build_resilience_report.py"
    spec = importlib.util.spec_from_file_location("osr_resilience_report", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_report_contains_only_bounded_aggregate_evidence(tmp_path: Path) -> None:
    module = _load_module()
    junit = tmp_path / "junit.xml"
    junit.write_text(
        '<testsuite tests="3" failures="0" errors="0" skipped="0" time="1.234">'
        '<testcase classname="hidden" name="hidden" time="0.1" />'
        "</testsuite>",
        encoding="utf-8",
    )
    report = module.build_report(
        junit,
        pytest_exit_code=0,
        source_sha="a" * 40,
    )
    assert report["status"] == "pass"
    assert report["counts"] == {"tests": 3, "failures": 0, "errors": 0, "skipped": 0}
    assert report["external_effects"] is False
    assert report["production_data_used"] is False
    encoded = json.dumps(report, sort_keys=True)
    assert "hidden" not in encoded
    assert len(encoded.encode("utf-8")) < 8192


def test_report_fails_closed_on_failed_or_incomplete_suite(tmp_path: Path) -> None:
    module = _load_module()
    junit = tmp_path / "junit.xml"
    junit.write_text(
        '<testsuite tests="2" failures="1" errors="0" skipped="0" time="0.5" />',
        encoding="utf-8",
    )
    report = module.build_report(junit, pytest_exit_code=1, source_sha="not-a-sha")
    assert report["status"] == "fail"
    assert report["source_sha"] == "unknown"


def test_report_fails_closed_when_required_scenarios_are_skipped(tmp_path: Path) -> None:
    module = _load_module()
    junit = tmp_path / "junit.xml"
    junit.write_text(
        '<testsuite tests="3" failures="0" errors="0" skipped="3" time="0.1" />',
        encoding="utf-8",
    )
    report = module.build_report(junit, pytest_exit_code=0, source_sha="b" * 40)
    assert report["status"] == "fail"
    assert report["counts"]["skipped"] == 3


def test_report_fails_closed_without_exact_source_identity(tmp_path: Path) -> None:
    module = _load_module()
    junit = tmp_path / "junit.xml"
    junit.write_text(
        '<testsuite tests="3" failures="0" errors="0" skipped="0" time="0.1" />',
        encoding="utf-8",
    )
    report = module.build_report(junit, pytest_exit_code=0, source_sha="not-a-sha")
    assert report["status"] == "fail"
    assert report["source_sha"] == "unknown"
