from __future__ import annotations

import logging

LOGGER = logging.getLogger("nexusdesk")


def record_fast_reply_metric(*, status: str, intent: str | None = None, handoff_required: bool | None = None, elapsed_ms: int | None = None) -> None:
    # Keep this as a low-cardinality logging shim for now. Prometheus integration
    # can wire these values into counters/histograms without changing call sites.
    LOGGER.info(
        "webchat_fast_reply_metric",
        extra={"event_payload": {
            "status": status,
            "intent": intent,
            "handoff_required": handoff_required,
            "elapsed_ms": elapsed_ms,
        }},
    )


def record_codex_app_server_metric(
    *,
    status: str,
    route: str,
    elapsed_ms: int | None = None,
    error_code: str | None = None,
) -> None:
    LOGGER.info(
        "webchat_codex_app_server_metric",
        extra={"event_payload": {
            "status": status,
            "route": route,
            "elapsed_ms": elapsed_ms,
            "error_code": error_code,
        }},
    )
