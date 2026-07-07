from __future__ import annotations

import time
from typing import Any

from ..tracking_fact_schema import TrackingFactEvent, TrackingFactResult, safe_tracking_candidate
from ..tool_governance import record_tool_call
from .adapter import SpeedafCoreAdapter, safe_query_summary
from .track_query import SpeedafTrackQueryClient, SpeedafTrackQueryError

HYBRID_TRACKING_SOURCE = "speedaf_api.hybrid_order_query_plus_track_query"


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


def _event_key(event: TrackingFactEvent) -> tuple[str, str, str]:
    return (
        (event.description or "").strip(),
        (event.location or "").strip(),
        (event.event_time or "").strip(),
    )


def merge_speedaf_hybrid_tracking_fact(
    *,
    primary: TrackingFactResult,
    history: TrackingFactResult,
) -> TrackingFactResult:
    """Merge current-status and full-history facts without letting history replace truth.

    `/mcp/order/query` remains the primary source for current status. The
    express track query contributes only event history. If either side is not
    trustworthy, the primary fact is returned unchanged.
    """

    if not (primary.ok and primary.fact_evidence_present):
        return primary
    if not (history.ok and history.fact_evidence_present):
        return primary

    merged_events: list[TrackingFactEvent] = []
    seen: set[tuple[str, str, str]] = set()
    for event in [*(history.events_summary or []), *(([history.latest_event] if history.latest_event else [])), *(([primary.latest_event] if primary.latest_event else [])), *(primary.events_summary or [])]:
        if event is None or not event.is_present():
            continue
        key = _event_key(event)
        if key in seen:
            continue
        seen.add(key)
        merged_events.append(event)
        if len(merged_events) >= 5:
            break

    latest_event = history.latest_event if history.latest_event and history.latest_event.is_present() else primary.latest_event
    tracking_number = primary.tracking_number or history.tracking_number

    return TrackingFactResult(
        ok=True,
        tracking_number=tracking_number,
        status=primary.status,
        status_label=primary.status_label or primary.status,
        latest_event=latest_event,
        events_summary=merged_events or primary.events_summary,
        checked_at=primary.checked_at or history.checked_at,
        source=HYBRID_TRACKING_SOURCE,
        tool_name=primary.tool_name,
        tool_status="success",
        pii_redacted=primary.pii_redacted and history.pii_redacted,
        fact_evidence_present=True,
        lifecycle_summary=history.lifecycle_summary or primary.lifecycle_summary,
        status_context=primary.status_context or history.status_context,
    )


def lookup_speedaf_track_history_fact(
    *,
    tracking_number: str | None,
    conversation_id: int | str | None = None,
    ticket_id: int | str | None = None,
    request_id: str | None = None,
    client: SpeedafTrackQueryClient | None = None,
) -> TrackingFactResult:
    """Resolve a Speedaf tracking fact from /open-api/express/track/query.

    This is a read-only full-track-history source. It is intentionally separate
    from the MCP order/query lookup and is enabled only when the caller selects
    WEBCHAT_TRACKING_FACT_SOURCE=speedaf_track_query.
    """

    started = time.monotonic()
    tracking = (tracking_number or "").strip().upper()
    safe_ticket_id = _safe_int(ticket_id)
    safe_webchat_conversation_id = _safe_int(conversation_id)
    resolved_client = client or SpeedafTrackQueryClient()

    if not tracking:
        result = TrackingFactResult(
            ok=False,
            tool_status="skipped",
            source="speedaf_api.express_track_query",
            tool_name="speedaf.express.track.query",
            pii_redacted=True,
            fact_evidence_present=False,
            failure_reason="missing_tracking_number",
        )
        record_tool_call(
            tool_name="speedaf.express.track.query",
            provider="speedaf_track_query",
            tool_type="read_only",
            input_payload={},
            output_payload=result.metadata_payload(),
            status="skipped",
            error_code=result.failure_reason,
            error_message=result.failure_reason,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            conversation_id=str(conversation_id) if conversation_id is not None else None,
            webchat_conversation_id=safe_webchat_conversation_id,
            ticket_id=safe_ticket_id,
            request_id=request_id,
        )
        return result

    try:
        history = resolved_client.query_history(tracking)
        result = history.to_tracking_fact()
    except SpeedafTrackQueryError as exc:
        result = TrackingFactResult(
            ok=False,
            tracking_number=tracking,
            tool_status="error",
            source="speedaf_api.express_track_query",
            tool_name="speedaf.express.track.query",
            pii_redacted=True,
            fact_evidence_present=False,
            failure_reason=exc.error.code,
        )
        record_tool_call(
            tool_name="speedaf.express.track.query",
            provider="speedaf_track_query",
            tool_type="read_only",
            input_payload=safe_query_summary(waybill_code=tracking),
            output_payload={"failure_reason": exc.error.code, **result.metadata_payload()},
            status="failed",
            error_code=exc.error.code,
            error_message=exc.error.message,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            conversation_id=str(conversation_id) if conversation_id is not None else None,
            webchat_conversation_id=safe_webchat_conversation_id,
            ticket_id=safe_ticket_id,
            request_id=request_id,
        )
        return result
    except Exception as exc:
        result = TrackingFactResult(
            ok=False,
            tracking_number=tracking,
            tool_status="error",
            source="speedaf_api.express_track_query",
            tool_name="speedaf.express.track.query",
            pii_redacted=True,
            fact_evidence_present=False,
            failure_reason=type(exc).__name__,
        )
        record_tool_call(
            tool_name="speedaf.express.track.query",
            provider="speedaf_track_query",
            tool_type="read_only",
            input_payload=safe_query_summary(waybill_code=tracking),
            output_payload={"failure_reason": type(exc).__name__, **result.metadata_payload()},
            status="failed",
            error_code=type(exc).__name__,
            error_message=str(exc),
            elapsed_ms=int((time.monotonic() - started) * 1000),
            conversation_id=str(conversation_id) if conversation_id is not None else None,
            webchat_conversation_id=safe_webchat_conversation_id,
            ticket_id=safe_ticket_id,
            request_id=request_id,
        )
        return result

    record_tool_call(
        tool_name="speedaf.express.track.query",
        provider="speedaf_track_query",
        tool_type="read_only",
        input_payload=safe_query_summary(waybill_code=tracking),
        output_payload=result.metadata_payload(),
        status="success" if result.ok else "failed",
        error_code=None if result.ok else result.failure_reason,
        error_message=result.failure_reason,
        elapsed_ms=int((time.monotonic() - started) * 1000),
        conversation_id=str(conversation_id) if conversation_id is not None else None,
        webchat_conversation_id=safe_webchat_conversation_id,
        ticket_id=safe_ticket_id,
        request_id=request_id,
    )
    return result


def lookup_speedaf_hybrid_tracking_fact(
    *,
    tracking_number: str | None,
    caller_id: str | None = None,
    country_code: str | None = None,
    conversation_id: int | str | None = None,
    ticket_id: int | str | None = None,
    request_id: str | None = None,
    adapter: SpeedafCoreAdapter | None = None,
    track_client: SpeedafTrackQueryClient | None = None,
) -> TrackingFactResult:
    """Resolve current status from order/query and optionally enrich with history.

    This is the production-safe hybrid mode:
    - current status comes from `/open-api/mcp/order/query`;
    - track history comes from `/open-api/express/track/query` when configured;
    - history failures are non-fatal and fall back to the primary fact.
    """

    primary = lookup_speedaf_tracking_fact(
        tracking_number=tracking_number,
        caller_id=caller_id,
        country_code=country_code,
        conversation_id=conversation_id,
        ticket_id=ticket_id,
        request_id=request_id,
        adapter=adapter,
    )

    resolved_tracking = (primary.tracking_number or tracking_number or "").strip().upper()
    if not resolved_tracking:
        return primary

    resolved_client = track_client or SpeedafTrackQueryClient()
    if not resolved_client.config.configured:
        return primary

    if not (primary.ok and primary.fact_evidence_present):
        history_fallback = lookup_speedaf_track_history_fact(
            tracking_number=resolved_tracking,
            conversation_id=conversation_id,
            ticket_id=ticket_id,
            request_id=request_id,
            client=resolved_client,
        )
        return history_fallback if history_fallback.ok and history_fallback.fact_evidence_present else primary

    history = lookup_speedaf_track_history_fact(
        tracking_number=resolved_tracking,
        conversation_id=conversation_id,
        ticket_id=ticket_id,
        request_id=request_id,
        client=resolved_client,
    )
    return merge_speedaf_hybrid_tracking_fact(primary=primary, history=history)


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
