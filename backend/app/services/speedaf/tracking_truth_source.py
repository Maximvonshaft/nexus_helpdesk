from __future__ import annotations

from datetime import datetime, timezone

from ..tracking_fact_schema import TrackingFactEvent, TrackingFactResult
from ..tracking_truth_contract import (
    AUTHORITY_ENRICHMENT,
    AUTHORITY_PRIMARY,
    EVIDENCE_CONTRADICTORY,
    EVIDENCE_NO_EVIDENCE,
    EVIDENCE_STALE,
    EVIDENCE_TIMEOUT,
    EVIDENCE_UNAVAILABLE,
    TrackingTruthResult,
    as_truth_result,
    evidence_state_for,
    safe_used_source,
)
from .adapter import SpeedafCoreAdapter
from .track_query import SpeedafTrackQueryClient
from .tracking_fact_source import (
    HYBRID_TRACKING_SOURCE,
    lookup_speedaf_track_history_fact,
    lookup_speedaf_tracking_fact,
)

DEFAULT_HISTORY_STALE_AFTER_SECONDS = 7 * 24 * 60 * 60


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
    values = [
        event.event_time
        for event in [result.latest_event, *result.events_summary]
        if event is not None and event.is_present() and event.event_time
    ]
    parsed = [(value, _parse_time(value)) for value in values]
    parsed = [(value, stamp) for value, stamp in parsed if stamp is not None]
    return max(parsed, key=lambda item: item[1])[0] if parsed else result.checked_at


def _history_state(
    history: TrackingFactResult,
    *,
    now: datetime,
    stale_after_seconds: int,
) -> tuple[str, str, str | None]:
    observed_at = _observed_at(history)
    observed = _parse_time(observed_at)
    if history.fact_evidence_present and observed is not None:
        if max(0.0, (now - observed).total_seconds()) > stale_after_seconds:
            return EVIDENCE_STALE, "stale", observed_at
        return "available", "fresh", observed_at
    state = evidence_state_for(history)
    if state == EVIDENCE_TIMEOUT:
        return EVIDENCE_TIMEOUT, "unknown", observed_at
    if state == EVIDENCE_UNAVAILABLE:
        return EVIDENCE_UNAVAILABLE, "unknown", observed_at
    return EVIDENCE_NO_EVIDENCE, "unknown", observed_at


def _events(primary: TrackingFactResult, history: TrackingFactResult) -> list[TrackingFactEvent]:
    merged: list[TrackingFactEvent] = []
    seen: set[tuple[str, str, str]] = set()
    for event in [primary.latest_event, *primary.events_summary, history.latest_event, *history.events_summary]:
        if event is None or not event.is_present():
            continue
        key = (
            (event.description or "").strip(),
            (event.location or "").strip(),
            (event.event_time or "").strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(event)
        if len(merged) >= 5:
            break
    return merged


def _contradictions(primary: TrackingFactResult, history: TrackingFactResult) -> list[dict[str, str]]:
    primary_status = (primary.status or primary.status_label or "").strip()
    history_status = (history.status or history.status_label or "").strip()
    if not primary_status or not history_status or primary_status == history_status:
        return []
    return [{
        "kind": "history_status_conflict",
        "resolution": "primary_current_status_preserved",
        "primary_tool": primary.tool_name,
        "history_tool": history.tool_name,
    }]


def merge_contract_safe_hybrid_tracking_fact(
    *,
    primary: TrackingFactResult,
    history: TrackingFactResult,
    now: datetime | None = None,
    stale_after_seconds: int = DEFAULT_HISTORY_STALE_AFTER_SECONDS,
) -> TrackingTruthResult:
    primary_truth = as_truth_result(primary, authority=AUTHORITY_PRIMARY)
    if not (primary_truth.ok and primary_truth.fact_evidence_present):
        return primary_truth

    resolved_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    history_state, history_freshness, history_observed_at = _history_state(
        history,
        now=resolved_now,
        stale_after_seconds=max(1, stale_after_seconds),
    )
    conflicts = _contradictions(primary_truth, history) if history.fact_evidence_present else []
    used_sources = [
        *primary_truth.used_sources,
        safe_used_source(
            source=history.source,
            tool_name=history.tool_name,
            authority=AUTHORITY_ENRICHMENT,
            evidence_state=history_state,
            observed_at=history_observed_at,
            freshness=history_freshness,
        ),
    ]
    return as_truth_result(
        primary_truth,
        authority=AUTHORITY_PRIMARY,
        evidence_state=EVIDENCE_CONTRADICTORY if conflicts else primary_truth.evidence_state,
        observed_at=primary_truth.observed_at,
        freshness=primary_truth.freshness,
        used_sources=used_sources,
        contradictions=conflicts,
        source=HYBRID_TRACKING_SOURCE,
        status=primary_truth.status,
        status_label=primary_truth.status_label or primary_truth.status,
        latest_event=primary_truth.latest_event,
        events_summary=_events(primary_truth, history) if history.fact_evidence_present else list(primary_truth.events_summary),
        lifecycle_summary=history.lifecycle_summary or primary_truth.lifecycle_summary,
        status_context=primary_truth.status_context,
    )


def lookup_speedaf_contract_safe_hybrid_tracking_fact(
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
) -> TrackingTruthResult:
    primary = lookup_speedaf_tracking_fact(
        tracking_number=tracking_number,
        caller_id=caller_id,
        country_code=country_code,
        conversation_id=conversation_id,
        ticket_id=ticket_id,
        request_id=request_id,
        adapter=adapter,
    )
    primary_truth = as_truth_result(primary, authority=AUTHORITY_PRIMARY)
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
        return merge_contract_safe_hybrid_tracking_fact(
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
    return merge_contract_safe_hybrid_tracking_fact(
        primary=primary_truth,
        history=history,
        now=now,
        stale_after_seconds=history_stale_after_seconds,
    )
