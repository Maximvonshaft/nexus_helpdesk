from __future__ import annotations

import re


def _redact(value: str) -> str:
    value = re.sub(
        r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]{12,}",
        "Bearer [redacted]",
        value,
    )
    return re.sub(
        r"\bsk-(?:proj-)?[A-Za-z0-9_-]{12,}\b",
        "sk-[redacted]",
        value,
    )


def pytest_runtest_logreport(report) -> None:
    """Temporary bounded diagnostics; remove with the root-cause fix."""

    if report.when != "call" or not report.failed:
        return
    detail = _redact(getattr(report, "longreprtext", ""))[-12000:]
    print(f"\nNEXUS_DIAGNOSTIC_FAILURE_NODE={report.nodeid}\n{detail}\n", flush=True)
