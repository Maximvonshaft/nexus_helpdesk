from __future__ import annotations

import time
from typing import Any

from ..tracking_fact_schema import TrackingFactResult, safe_tracking_candidate
from ..tool_governance import record_tool_call
from .adapter import SpeedafCoreAdapter, safe_query_summary


def _safe_int(value: int | str | None) -> int | None:
    return int(value) if isinstance(value, int) or (isinstance(value, str) and value.isdigit()) else None


def _safe_candidate_payload(candidate) -> dict[str, str]:
    waybill = getattr(candidate, "waybill_code", None)
    suffix = getattr(candidate, "waybill_code_suffix", None)
    return safe_tracking_candidate(waybill, suffix)


def _record_waybill_lookup(
    *,
    result,
    caller_id: str | None,
    country_code: str | None,
    conversation_id: int | str | None,
    ticket_id: int | str | None,
    request_id: str | None,
    elapsed_ms: int,
) -> None:
    record_tool_call(
        tool_name="speedaf.order.waybill_code.query",
        provider="speedaf_mcp",
        tool_type="read_only",
        input_payload={"caller": "redacted", "country_code": (country_code or "CH").upper()},
        output_payload={"count": len(result.candidates), "safe_candidates": [_safe_candidate_payload(item) for item in result.candidates[:10]]},
        status="success" if result.ok else "failed",
        error_code=result.failure_reason,
        error_message=result.failure_reason,
        elapsed_ms=elapsed_ms,
        conversation_id=str(conversation_id) if conversation_id is not None else None,
        webchat_conversation_id=_safe_int(conversation_id),
        ticket_id=_safe_int(ticket_id),
        request_id=request_id,
    )


def lookup_speedaf_tracking_fact(
    *,
    tracking_number: str | None,
    caller_id: str | None = None,
    country_code: str | None = None,
    conversation_id: int | str | None = None,
    ticket_id: int | str | None = None,
    request_id: str | None = None,
    adapter: SpeedafCoreAdapter | None = None,
) -> TrackingFactResult:
    """Resolve a Speedaf tracking fact using the official MCP adapter.

    If a tracking number is missing but callerID is available, this first uses
    the read-only waybillCode/query interface. A single candidate is resolved to
    order/query; multiple candidates are returned as safe suffix/hash entries.
    """

    started = time.monotonic()
    tracking = (tracking_number or "").strip().upper()
    caller = (caller_id or "").strip()
    safe_ticket_id = _safe_int(ticket_id)
    safe_webchat_conversation_id = _safe_int(conversation_id)
    resolved_adapter = adapter or SpeedafCoreAdapter()

    if not tracking:
        if not caller:
            return TrackingFactResult(
                ok=False,
                tool_status="skipped",
                source="speedaf_api.order_query",
                tool_name="speedaf.order.query",
                pii_redacted=True,
                fact_evidence_present=False,
                failure_reason="missing_tracking_number",
            )
        lookup_started = time.monotonic()
        try:
            lookup = resolved_adapter.query_waybills_by_caller(caller_id=caller, country_code=country_code)
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - lookup_started) * 1000)
            record_tool_call(
                tool_name="speedaf.order.waybill_code.query",
                provider="speedaf_mcp",
                tool_type="read_only",
                input_payload={"caller": "redacted", "country_code": (country_code or "CH").upper()},
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
            return TrackingFactResult(ok=False, tool_status="error", source="speedaf_api.waybill_code_query", tool_name="speedaf.order.waybill_code.query", pii_redacted=True, fact_evidence_present=False, failure_reason=type(exc).__name__)
        elapsed_ms = int((time.monotonic() - lookup_started) * 1000)
        _record_waybill_lookup(result=lookup, caller_id=caller, country_code=country_code, conversation_id=conversation_id, ticket_id=ticket_id, request_id=request_id, elapsed_ms=elapsed_ms)
        if not lookup.ok:
            return TrackingFactResult(ok=False, tool_status="failed", source="speedaf_api.waybill_code_query", tool_name="speedaf.order.waybill_code.query", pii_redacted=True, fact_evidence_present=False, failure_reason=lookup.failure_reason or "waybill_lookup_failed")
        if len(lookup.candidates) == 0:
            return TrackingFactResult(ok=False, tool_status="not_found", source="speedaf_api.waybill_code_query", tool_name="speedaf.order.waybill_code.query", pii_redacted=True, fact_evidence_present=False, failure_reason="waybill_not_found_by_caller")
        if len(lookup.candidates) > 1:
            return TrackingFactResult(
                ok=False,
                tool_status="needs_customer_selection",
                source="speedaf_api.waybill_code_query",
                tool_name="speedaf.order.waybill_code.query",
                pii_redacted=True,
                fact_evidence_present=False,
                failure_reason="multiple_waybill_candidates",
                safe_candidates=[_safe_candidate_payload(item) for item in lookup.candidates[:10]],
            )
        tracking = (getattr(lookup.candidates[0], "waybill_code", "") or "").strip().upper()
        if not tracking:
            return TrackingFactResult(ok=False, tool_status="not_found", source="speedaf_api.waybill_code_query", tool_name="speedaf.order.waybill_code.query", pii_redacted=True, fact_evidence_present=False, failure_reason="waybill_not_found_by_caller")

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
