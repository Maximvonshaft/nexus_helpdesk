from __future__ import annotations

from typing import Any

from ..observability import _counter, _histogram, _label

_AGENT_RUNS = _counter(
    "nexusdesk_agent_runs_total",
    "Canonical Agent Run terminal outcomes",
    ["status", "final_action"],
)
_AGENT_RUN_DURATION = _histogram(
    "nexusdesk_agent_run_duration_ms",
    "Canonical Agent Run duration in milliseconds",
    ["status"],
    (50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, 60000, 120000),
)
_AGENT_EVENTS = _counter(
    "nexusdesk_agent_run_events_total",
    "Append-only Agent Run event count",
    ["event_type", "status"],
)
_AGENT_CONTEXT = _histogram(
    "nexusdesk_agent_context_estimated_tokens",
    "Provider-neutral estimated Agent context tokens",
    ["compacted"],
    (128, 256, 512, 1024, 2048, 4096, 8192, 16384, 32768),
)
_AGENT_TOOL_DURATION = _histogram(
    "nexusdesk_agent_tool_duration_ms",
    "Agent-observed canonical Tool duration in milliseconds",
    ["tool_name", "status"],
    (1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, 60000),
)
_AGENT_FALLBACKS = _counter(
    "nexusdesk_agent_fallback_total",
    "Agent terminal fallback outcomes",
    ["error_category"],
)


def record_agent_event(event_type: str, status: str = "recorded") -> None:
    if _AGENT_EVENTS:
        _AGENT_EVENTS.labels(
            event_type=_label(event_type),
            status=_label(status),
        ).inc()


def record_agent_run(
    *,
    status: str,
    final_action: str | None,
    elapsed_ms: int | float,
) -> None:
    safe_status = _label(status)
    if _AGENT_RUNS:
        _AGENT_RUNS.labels(
            status=safe_status,
            final_action=_label(final_action, "none"),
        ).inc()
    if _AGENT_RUN_DURATION:
        _AGENT_RUN_DURATION.labels(status=safe_status).observe(
            max(0.0, float(elapsed_ms or 0))
        )


def record_context_compilation(summary: dict[str, Any] | None) -> None:
    if not _AGENT_CONTEXT or not isinstance(summary, dict):
        return
    estimated = summary.get("estimated_tokens")
    if not isinstance(estimated, (int, float)):
        return
    _AGENT_CONTEXT.labels(
        compacted="true" if summary.get("compacted") is True else "false"
    ).observe(max(0.0, float(estimated)))


def record_agent_tool(
    *,
    tool_name: str,
    status: str,
    elapsed_ms: int | float,
) -> None:
    if _AGENT_TOOL_DURATION:
        _AGENT_TOOL_DURATION.labels(
            tool_name=_label(tool_name),
            status=_label(status),
        ).observe(max(0.0, float(elapsed_ms or 0)))


def record_agent_fallback(error_code: str | None) -> None:
    if not _AGENT_FALLBACKS:
        return
    candidate = str(error_code or "unknown").lower()
    if "timeout" in candidate:
        category = "timeout"
    elif "tool" in candidate:
        category = "tool"
    elif "provider" in candidate:
        category = "provider"
    elif "release" in candidate or "deployment" in candidate:
        category = "release"
    elif "contract" in candidate or "invalid" in candidate:
        category = "contract"
    else:
        category = "other"
    _AGENT_FALLBACKS.labels(error_category=category).inc()
