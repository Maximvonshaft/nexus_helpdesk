from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

_STATE = {
    "collected": 0,
    "passed": 0,
    "failed": 0,
    "skipped": 0,
    "errors": 0,
}


def pytest_collection_finish(session) -> None:  # noqa: ANN001
    _STATE["collected"] = len(session.items)


def pytest_runtest_logreport(report) -> None:  # noqa: ANN001
    if report.when not in {"setup", "call"}:
        return
    if report.skipped:
        _STATE["skipped"] += 1
    elif report.failed:
        if report.when == "setup":
            _STATE["errors"] += 1
        else:
            _STATE["failed"] += 1
    elif report.when == "call" and report.passed:
        _STATE["passed"] += 1


def _write_receipt(*, status: str, reasons: list[str]) -> None:
    path_value = str(os.getenv("NEXUS_POSTGRES_PYTEST_RECEIPT") or "").strip()
    if not path_value:
        return
    path = Path(path_value).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "nexus.postgres-pytest-gate.v1",
        "status": status,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "counts": dict(_STATE),
        "reason_codes": reasons,
        "skip_policy": "forbidden",
        "zero_collection_policy": "forbidden",
        "sanitized": True,
        "contains_customer_data": False,
        "contains_secrets": False,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def pytest_sessionfinish(session, exitstatus: int) -> None:  # noqa: ANN001
    reasons: list[str] = []
    if _STATE["collected"] <= 0:
        reasons.append("postgres_pytest_zero_collection")
    if _STATE["skipped"] > 0:
        reasons.append("postgres_pytest_skip_forbidden")
    if _STATE["failed"] > 0 or _STATE["errors"] > 0 or exitstatus != 0:
        reasons.append("postgres_pytest_failure")
    if reasons:
        session.exitstatus = 1
        _write_receipt(status="fail", reasons=sorted(set(reasons)))
        return
    _write_receipt(status="pass", reasons=[])
