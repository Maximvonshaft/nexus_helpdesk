from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from ..tracking_fact_schema import (
    EVIDENCE_AVAILABLE,
    EVIDENCE_CONTRADICTORY,
    EVIDENCE_STALE,
    FRESHNESS_FRESH,
    FRESHNESS_STALE,
    FRESHNESS_UNKNOWN,
    SOURCE_AUTHORITY_ENRICHMENT,
    SOURCE_AUTHORITY_PRIMARY,
    TrackingFactEvent,
    TrackingFactResult,
    as_tracking_truth_result,
    evidence_state_for,
    safe_tracking_candidate,
    safe_used_source,
)
from ..tool_governance import record_tool_call
from .adapter import SpeedafCoreAdapter, safe_query_summary
from .track_query import SpeedafTrackQueryClient, SpeedafTrackQueryError

HYBRID_TRACKING_SOURCE = "speedaf_api.hybrid_order_query_plus_track_query"
DEFAULT_HISTORY_STALE_AFTER_SECONDS = 7 * 24 * 60 * 60


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
        output_payload={
            "count": len(result.candidates),
            "safe_candidates": [_safe_candidate_payload(item) for item in result.candidates[:10]],
        },
        status="success" if result.ok else "failed",
        error_code=(result.failure_reason or "waybill_lookup_failed")[:120] if not result.ok else None,
        error_message=(result.failure_reason or "waybill_lookup_failed")[:120] if not result.ok else None,
        elapsed_ms=elapsed_ms,
        conversation_id=str(conversation_id) if conversation_id is not None else None,
        webchat_conversation_id=_safe_int(conversation_id),
        ticket_id=_safe_int(ticket_id),
        request_id=str(request_id)[:120] if request_id else None,
    )


def _parse_time(value: str | None) -> datetime | None:
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    for candidate in (cleaned.replace("Z", "+00:00"), cleaned):
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            try:
                parsed = datetime.strptime(candidate, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def _observed_at(result: TrackingFactResult) -> str | None:
    candidates: list[tuple[str, datetime]] = []
    for event in [result.latest_event, *result.events_summary]:
        if event is None or not event.is_present() or not event.event_time:
            continue
        parsed = _parse_time(event.event_time)
        if parsed is not None:
            candidates.append((event.event_time, parsed))
    if candidates:
        return max(candidates, key=lambda item: item[1])[0]
    return result.observed_at or result.checked_at


def _event_key(event: TrackingFactEvent) -> tuple[str, str, str]:
    return (
        (event.description or "").strip(),
        (event.location or "").strip(),
        (event.event_time or "").strip(),
    )


def _merged_events(primary: TrackingFactResult, history: TrackingFactResult) -> list[TrackingFactEvent]:
    merged: list[TrackingFactEvent] = []
    seen: set[tuple[str, str, str]] = set()
    for event in [primary.latest_event, *primary.events_summary, history.latest_event, *history.events_summary]:
        if event is None or not event.is_present():
            continue
        key = _event_key(event)
        if key in seen:
            continue
        seen.add(key)
        merged.append(event)
        if len(merged) >= 5:
            break
    return merged


def _history_evidence(
    history: TrackingFactResult,
    *,
    now: datetime,
    stale_after_seconds: int,
) -> tuple[str, str, str | None]:
    observed_at = _observed_at(history)
    observed = _parse_time(observed_at)
    if history.fact_evidence_present:
        if observed is not None and max(0.0, (now - observed).total_seconds()) > stale_after_seconds:
            return EVIDENCE_STALE, FRESHNESS_STALE, observed_at
        return EVIDENCE_AVAILABLE, FRESHNESS_FRESH, observed_at
    state = evidence_state_for(
        fact_evidence_present=False,
        failure_reason=history.failure_reason,
        tool_status=history.tool_status,
    )
    return state, FRESHNESS_UNKNOWN, observed_at


def _contradictions(primary: TrackingFactResult, history: TrackingFactResult) -> list[dict[str, str]]:
    primary_status = (primary.status or primary.status_label or "").strip()
    history_status = (history.status or history.status_label or "").strip()
    if not primary_status or not history_status or primary_status == history_status:
        return []
    return [
        {
            "kind": "history_status_conflict",
            "resolution": "primary_current_status_preserved",
            "primary_tool": primary.tool_name,
            "history_tool": history.tool_name,
        }
    ]


def merge_speedaf_hybrid_tracking_fact(
    *,
    primary: TrackingFactResult,
    history: TrackingFactResult,
    now: datetime | None = None,
    stale_after_seconds: int = DEFAULT_HISTORY_STALE_AFTER_SECONDS,
) -> TrackingFactResult:
    """Preserve approved live truth and add history only as explicit enrichment."""

    primary_truth = as_tracking_truth_result(
        primary,
        authority=SOURCE_AUTHORITY_PRIMARY,
        observed_at=_observed_at(primary),
    )
    if not (primary_truth.ok and primary_truth.fact_evidence_present):
        return primary_truth

    resolved_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    history_state, history_freshness, history_observed_at = _history_evidence(
        history,
        now=resolved_now,
        stale_after_seconds=max(1, stale_after_seconds),
    )
    conflicts = _contradictions(primary_truth, history) if history.fact_evidence_present else []
    history_source = safe_used_source(
        source=history.source,
        tool_name=history.tool_name,
        authority=SOURCE_AUTHORITY_ENRICHMENT,
        evidence_state=history_state,
        observed_at=history_observed_at,
        freshness=history_freshness,
    )
    used_sources = [*primary_truth.used_sources, history_source]

    return as_tracking_truth_result(
        primary_truth,
        authority=SOURCE_AUTHORITY_PRIMARY,
        evidence_state=EVIDENCE_CONTRADICTORY if conflicts else primary_truth.evidence_state,
        observed_at=primary_truth.observed_at,
        freshness=primary_truth.freshness,
        used_sources=used_sources,
        contradictions=conflicts,
        source=HYBRID_TRACKING_SOURCE,
        status=primary_truth.status,
        status_label=primary_truth.status_label or primary_truth.status,
        latest_event=primary_truth.latest_event,
        events_summary=(
            _merged_events(primary_truth, history)
            if history.fact_evidence_present
            else list(primary_truth.events_summary)
        ),
        lifecycle_summary=(history.lifecycle_summary or primary_truth.lifecycle_summary),
        status_context=primary_truth.status_context,
        pii_redacted=primary_truth.pii_redacted,
        fact_evidence_present=True,
        ok=True,
        tool_status="success",
    )


def lookup_speedaf_track_history_fact(
    *,
    tracking_number: str | None,
    conversation_id: int | str | None = None,
    ticket_id: int | str | None = None,
    request_id: str | None = None,
    client: SpeedafTrackQueryClient | None = None,
) -> TrackingFactResult:
    """Return structured express-history evidence; this is never primary truth."""

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
            request_id=str(request_id)[:120] if request_id else None,
        )
        return result

    try:
        history = resolved_client.query_history(tracking)
        result = history.to_tracking_fact()
    except SpeedafTrackQueryError as exc:
        code = str(exc.error.code or "track_query_error")[:120]
        result = TrackingFactResult(
            ok=False,
            tracking_number=tracking,
            tool_status="error",
            source="speedaf_api.express_track_query",
            tool_name="speedaf.express.track.query",
            pii_redacted=True,
            fact_evidence_present=False,
            failure_reason=code,
        )
        record_tool_call(
            tool_name="speedaf.express.track.query",
            provider="speedaf_track_query",
            tool_type="read_only",
            input_payload=safe_query_summary(waybill_code=tracking),
            output_payload=result.metadata_payload(),
            status="failed",
            error_code=code,
            error_message=code,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            conversation_id=str(conversation_id) if conversation_id is not None else None,
            webchat_conversation_id=safe_webchat_conversation_id,
            ticket_id=safe_ticket_id,
            request_id=str(request_id)[:120] if request_id else None,
        )
        return result
    except Exception as exc:
        code = type(exc).__name__[:120]
        result = TrackingFactResult(
            ok=False,
            tracking_number=tracking,
            tool_status="error",
            source="speedaf_api.express_track_query",
            tool_name="speedaf.express.track.query",
            pii_redacted=True,
            fact_evidence_present=False,
            failure_reason=code,
        )
        record_tool_call(
            tool_name="speedaf.express.track.query",
            provider="speedaf_track_query",
            tool_type="read_only",
            input_payload=safe_query_summary(waybill_code=tracking),
            output_payload=result.metadata_payload(),
            status="failed",
            error_code=code,
            error_message=code,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            conversation_id=str(conversation_id) if conversation_id is not None else None,
            webchat_conversation_id=safe_webchat_conversation_id,
            ticket_id=safe_ticket_id,
            request_id=str(request_id)[:120] if request_id else None,
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
        error_message=None if result.ok else (result.failure_reason or "track_query_failed")[:120],
        elapsed_ms=int((time.monotonic() - started) * 1000),
        conversation_id=str(conversation_id) if conversation_id is not None else None,
        webchat_conversation_id=safe_webchat_conversation_id,
        ticket_id=safe_ticket_id,
        request_id=str(request_id)[:120] if request_id else None,
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
    now: datetime | None = None,
    history_stale_after_seconds: int = DEFAULT_HISTORY_STALE_AFTER_SECONDS,
) -> TrackingFactResult:
    """Resolve current status from MCP/order-query, then optionally enrich it."""

    primary = lookup_speedaf_tracking_fact(
        tracking_number=tracking_number,
        caller_id=caller_id,
        country_code=country_code,
        conversation_id=conversation_id,
        ticket_id=ticket_id,
        request_id=request_id,
        adapter=adapter,
    )
    primary_truth = as_tracking_truth_result(
        primary,
        authority=SOURCE_AUTHORITY_PRIMARY,
        observed_at=_observed_at(primary),
    )
    if not (primary_truth.ok and primary_truth.fact_evidence_present):
        return primary_truth

    resolved_tracking = (primary_truth.tracking_number or tracking_number or "").strip().upper()
    if not resolved_tracking:
        return primary_truth

    resolved_client = track_client or SpeedafTrackQueryClient()
    if not resolved_client.config.configured:
        unavailable = TrackingFactResult(
            ok=False,
            source="speedaf_api.express_track_query",
            tool_name="speedaf.express.track.query",
            tool_status="unavailable",
            pii_redacted=True,
            fact_evidence_present=False,
            failure_reason="history_source_unavailable",
        )
        return merge_speedaf_hybrid_tracking_fact(
            primary=primary_truth,
            history=unavailable,
            now=now,
            stale_after_seconds=history_stale_after_seconds,
        )

    history = lookup_speedaf_track_history_fact(
        tracking_number=resolved_tracking,
        conversation_id=conversation_id,
        ticket_id=ticket_id,
        request_id=request_id,
        client=resolved_client,
    )
    return merge_speedaf_hybrid_tracking_fact(
        primary=primary_truth,
        history=history,
        now=now,
        stale_after_seconds=history_stale_after_seconds,
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
    """Resolve a structured tracking fact using the approved Speedaf MCP adapter."""

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
            code = type(exc).__name__[:120]
            record_tool_call(
                tool_name="speedaf.order.waybill_code.query",
                provider="speedaf_mcp",
                tool_type="read_only",
                input_payload={"caller": "redacted", "country_code": (country_code or "CH").upper()},
                output_payload={"failure_reason": code},
                status="failed",
                error_code=code,
                error_message=code,
                elapsed_ms=elapsed_ms,
                conversation_id=str(conversation_id) if conversation_id is not None else None,
                webchat_conversation_id=safe_webchat_conversation_id,
                ticket_id=safe_ticket_id,
                request_id=str(request_id)[:120] if request_id else None,
            )
            return TrackingFactResult(
                ok=False,
                tool_status="error",
                source="speedaf_api.waybill_code_query",
                tool_name="speedaf.order.waybill_code.query",
                pii_redacted=True,
                fact_evidence_present=False,
                failure_reason=code,
            )
        elapsed_ms = int((time.monotonic() - lookup_started) * 1000)
        _record_waybill_lookup(
            result=lookup,
            caller_id=caller,
            country_code=country_code,
            conversation_id=conversation_id,
            ticket_id=ticket_id,
            request_id=request_id,
            elapsed_ms=elapsed_ms,
        )
        if not lookup.ok:
            return TrackingFactResult(
                ok=False,
                tool_status="failed",
                source="speedaf_api.waybill_code_query",
                tool_name="speedaf.order.waybill_code.query",
                pii_redacted=True,
                fact_evidence_present=False,
                failure_reason=lookup.failure_reason or "waybill_lookup_failed",
            )
        if len(lookup.candidates) == 0:
            return TrackingFactResult(
                ok=False,
                tool_status="not_found",
                source="speedaf_api.waybill_code_query",
                tool_name="speedaf.order.waybill_code.query",
                pii_redacted=True,
                fact_evidence_present=False,
                failure_reason="waybill_not_found_by_caller",
            )
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
            return TrackingFactResult(
                ok=False,
                tool_status="not_found",
                source="speedaf_api.waybill_code_query",
                tool_name="speedaf.order.waybill_code.query",
                pii_redacted=True,
                fact_evidence_present=False,
                failure_reason="waybill_not_found_by_caller",
            )

    try:
        result = resolved_adapter.query_order_tracking_fact(waybill_code=tracking, caller_id=caller_id)
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        code = type(exc).__name__[:120]
        record_tool_call(
            tool_name="speedaf.order.query",
            provider="speedaf_mcp",
            tool_type="read_only",
            input_payload=safe_query_summary(waybill_code=tracking, caller_id=caller_id),
            output_payload={"failure_reason": code},
            status="failed",
            error_code=code,
            error_message=code,
            elapsed_ms=elapsed_ms,
            conversation_id=str(conversation_id) if conversation_id is not None else None,
            webchat_conversation_id=safe_webchat_conversation_id,
            ticket_id=safe_ticket_id,
            request_id=str(request_id)[:120] if request_id else None,
        )
        return TrackingFactResult(
            ok=False,
            tracking_number=tracking,
            tool_status="error",
            source="speedaf_api.order_query",
            tool_name="speedaf.order.query",
            pii_redacted=True,
            fact_evidence_present=False,
            failure_reason=code,
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
        error_message=None if result.ok else (result.failure_reason or "tracking_lookup_failed")[:120],
        elapsed_ms=elapsed_ms,
        conversation_id=str(conversation_id) if conversation_id is not None else None,
        webchat_conversation_id=safe_webchat_conversation_id,
        ticket_id=safe_ticket_id,
        request_id=str(request_id)[:120] if request_id else None,
    )
    return result
