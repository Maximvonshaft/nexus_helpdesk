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


def record_openclaw_responses_metric(*, status: str, agent_id: str, elapsed_ms: int | None = None) -> None:
    LOGGER.info(
        "webchat_openclaw_responses_metric",
        extra={"event_payload": {
            "status": status,
            "agent_id": agent_id,
            "elapsed_ms": elapsed_ms,
        }},
    )
