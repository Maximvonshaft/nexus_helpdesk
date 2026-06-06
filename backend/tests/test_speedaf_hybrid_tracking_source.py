from __future__ import annotations

from app.services.speedaf.tracking_fact_source import (
    HYBRID_TRACKING_SOURCE,
    merge_speedaf_hybrid_tracking_fact,
)
from app.services.tracking_fact_schema import TrackingFactEvent, TrackingFactResult


def _primary_fact() -> TrackingFactResult:
    return TrackingFactResult(
        ok=True,
        tracking_number="CH120000005451",
        status="4",
        status_label="Speedaf status code 4",
        latest_event=TrackingFactEvent(description="Speedaf status code 4", location="一级网点"),
        events_summary=[TrackingFactEvent(description="Speedaf status code 4", location="一级网点")],
        source="speedaf_api.order_query",
        tool_name="speedaf.order.query",
        tool_status="success",
        pii_redacted=True,
        fact_evidence_present=True,
    )


def _history_fact() -> TrackingFactResult:
    return TrackingFactResult(
        ok=True,
        tracking_number="CH120000005451",
        status="-20",
        status_label="Returned to customer in origin country",
        latest_event=TrackingFactEvent(
            description="Returned to customer in origin country",
            event_time="2026-03-19 19:35:04",
        ),
        events_summary=[
            TrackingFactEvent(
                description="Returned to customer in origin country",
                event_time="2026-03-19 19:35:04",
            ),
            TrackingFactEvent(
                description="Parcel received at warehouse",
                event_time="2026-03-17 16:11:21",
            ),
        ],
        source="speedaf_api.express_track_query",
        tool_name="speedaf.express.track.query",
        tool_status="success",
        pii_redacted=True,
        fact_evidence_present=True,
    )


def test_hybrid_merge_preserves_order_query_current_status() -> None:
    merged = merge_speedaf_hybrid_tracking_fact(primary=_primary_fact(), history=_history_fact())
    summary = merged.prompt_summary()

    assert merged.ok is True
    assert merged.source == HYBRID_TRACKING_SOURCE
    assert merged.tool_name == "speedaf.order.query"
    assert merged.status == "4"
    assert merged.status_label == "Speedaf status code 4"
    assert merged.fact_evidence_present is True
    assert "Current status: Speedaf status code 4" in summary
    assert "Returned to customer in origin country" in summary
    assert "Parcel received at warehouse" in summary


def test_hybrid_merge_ignores_failed_history_and_returns_primary() -> None:
    primary = _primary_fact()
    failed_history = TrackingFactResult(
        ok=False,
        tracking_number="CH120000005451",
        source="speedaf_api.express_track_query",
        tool_name="speedaf.express.track.query",
        tool_status="error",
        pii_redacted=True,
        fact_evidence_present=False,
        failure_reason="timeout",
    )

    merged = merge_speedaf_hybrid_tracking_fact(primary=primary, history=failed_history)

    assert merged is primary
    assert merged.source == "speedaf_api.order_query"
    assert merged.status_label == "Speedaf status code 4"


def test_hybrid_merge_ignores_history_when_primary_is_not_trusted() -> None:
    primary = TrackingFactResult(
        ok=False,
        tracking_number="CH120000005451",
        source="speedaf_api.order_query",
        tool_name="speedaf.order.query",
        tool_status="error",
        pii_redacted=True,
        fact_evidence_present=False,
        failure_reason="waybill_not_found",
    )

    merged = merge_speedaf_hybrid_tracking_fact(primary=primary, history=_history_fact())

    assert merged is primary
    assert merged.fact_evidence_present is False
    assert merged.failure_reason == "waybill_not_found"
