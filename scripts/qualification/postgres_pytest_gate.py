from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _state(config) -> dict[str, Any]:  # noqa: ANN001
    state = getattr(config, "_nexus_postgres_gate", None)
    if state is None:
        state = {
            "collected": 0,
            "passed": 0,
            "failed": 0,
            "skipped": 0,
            "errors": 0,
        }
        config._nexus_postgres_gate = state
    return state


def pytest_collection_finish(session) -> None:  # noqa: ANN001
    _state(session.config)["collected"] = len(session.items)


def pytest_runtest_logreport(report) -> None:  # noqa: ANN001
    if report.when not in {"setup", "call"}:
        return
    state = _state(report.config)
    if report.skipped:
        state["skipped"] += 1
    elif report.failed:
        if report.when == "setup":
            state["errors"] += 1
        else:
            state["failed"] += 1
    elif report.when == "call" and report.passed:
        state["passed"] += 1


def _write_receipt(config, *, status: str, reasons: list[str]) -> None:  # noqa: ANN001
    path_value = str(os.getenv("NEXUS_POSTGRES_PYTEST_RECEIPT") or "").strip()
    if not path_value:
        return
    path = Path(path_value).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "nexus.postgres-pytest-gate.v1",
        "status": status,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "counts": dict(_state(config)),
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
    state = _state(session.config)
    reasons: list[str] = []
    if state["collected"] <= 0:
        reasons.append("postgres_pytest_zero_collection")
    if state["skipped"] > 0:
        reasons.append("postgres_pytest_skip_forbidden")
    if state["failed"] > 0 or state["errors"] > 0 or exitstatus != 0:
        reasons.append("postgres_pytest_failure")
    if reasons:
        session.exitstatus = 1
        _write_receipt(session.config, status="fail", reasons=sorted(set(reasons)))
        return
    _write_receipt(session.config, status="pass", reasons=[])
