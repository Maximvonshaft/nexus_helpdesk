from __future__ import annotations

import time
from typing import Any

from ..tracking_fact_schema import TrackingFactResult
from ..tool_governance import record_tool_call
from .adapter import SpeedafCoreAdapter, safe_query_summary


def lookup_speedaf_tracking_fact(
    *,
    tracking_number: str | None,
    caller_id: str | None = None,
    conversation_id: int | str | None = None,
    ticket_id: int | str | None = None,
    request_id: str | None = None,
    adapter: SpeedafCoreAdapter | None = None,
) -> TrackingFactResult:
    """Resolve a Speedaf tracking fact using the official MCP adapter.

    This helper is side-effect safe except for ToolCallLog audit. It returns a
    TrackingFactResult that can be injected into the existing WebChat fact gate.
    """

    started = time.monotonic()
    tracking = (tracking_number or "").strip().upper()
    if not tracking:
        return TrackingFactResult(
            ok=False,
            tool_status="skipped",
            source="speedaf_api.order_query",
            tool_name="speedaf.order.query",
            pii_redacted=True,
            fact_evidence_present=False,
            failure_reason="missing_tracking_number",
        )

    safe_ticket_id = int(ticket_id) if isinstance(ticket_id, int) or (isinstance(ticket_id, str) and ticket_id.isdigit()) else None
    safe_webchat_conversation_id = int(conversation_id) if isinstance(conversation_id, int) or (isinstance(conversation_id, str) and conversation_id.isdigit()) else None

    resolved_adapter = adapter or SpeedafCoreAdapter()
    try:
        result = resolved_adapter.query_order_tracking_fact(waybill_code=tracking, caller_id=caller_id)
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        record_tool_call(
            tool_name="speedaf.order.query",
            provider="speedaf_mcp",
            tool_type="read_only",
            input_payload=safe_query_summary(waybill_code=tracking, caller_id=caller_id),
            output_payload={"failure_reason": type(exc).__name__},
            status="failed",
            error_code=type(exc).__name__,
            error_message=str(exc),
            elapsed_ms=elapsed_ms,
            conversation_id=str(conversation_id) if conversation_id is not None else None,
            webchat_conversation_id=safe_webchat_conversation_id,
            ticket_id=safe_ticket_id,
            request_id=request_id,
        )
        return TrackingFactResult(
            ok=False,
            tracking_number=tracking,
            tool_status="error",
            source="speedaf_api.order_query",
            tool_name="speedaf.order.query",
            pii_redacted=True,
            fact_evidence_present=False,
            failure_reason=type(exc).__name__,
        )

    elapsed_ms = int((time.monotonic() - started) * 1000)
    output_payload: dict[str, Any] = result.metadata_payload()
    record_tool_call(
        tool_name="speedaf.order.query",
        provider="speedaf_mcp",
        tool_type="read_only",
        input_payload=safe_query_summary(waybill_code=tracking, caller_id=caller_id),
        output_payload=output_payload,
        status="success" if result.ok and result.fact_evidence_present else "failed",
        error_code=None if result.ok else result.failure_reason,
        error_message=result.failure_reason,
        elapsed_ms=elapsed_ms,
        conversation_id=str(conversation_id) if conversation_id is not None else None,
        webchat_conversation_id=safe_webchat_conversation_id,
        ticket_id=safe_ticket_id,
        request_id=request_id,
    )
    return result
