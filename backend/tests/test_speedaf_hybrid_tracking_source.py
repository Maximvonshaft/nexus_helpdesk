from __future__ import annotations

from types import SimpleNamespace

from app.services.speedaf.tracking_fact_source import (
    HYBRID_TRACKING_SOURCE,
    lookup_speedaf_track_history_fact,
    lookup_speedaf_hybrid_tracking_fact,
    merge_speedaf_hybrid_tracking_fact,
)
from app.services.speedaf.track_query import SpeedafTrackHistory
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
        lifecycle_summary={
            "latest_milestone": "delivered",
            "latest_action": "5",
            "durations": {"last_mile_hours": 2.5, "total_transit_hours": 50.0},
            "risk": {"escalate_required": False},
        },
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
    assert merged.lifecycle_summary["latest_milestone"] == "delivered"
    assert "Lifecycle: delivered | action 5" in summary
    assert "last_mile_hours=2.5" in summary


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


def test_hybrid_lookup_falls_back_to_track_query_when_primary_has_no_evidence(monkeypatch) -> None:
    primary = TrackingFactResult(
        ok=False,
        tracking_number="MK000179196R",
        source="speedaf_api.order_query",
        tool_name="speedaf.order.query",
        tool_status="error",
        pii_redacted=True,
        fact_evidence_present=False,
        failure_reason="tracking_lookup_no_match",
    )
    history = TrackingFactResult(
        ok=True,
        tracking_number="MK000179196R",
        status="5",
        status_label="Delivered",
        latest_event=TrackingFactEvent(description="Delivered", event_time="2026-07-01 10:00:00"),
        events_summary=[TrackingFactEvent(description="Delivered", event_time="2026-07-01 10:00:00")],
        source="speedaf_api.express_track_query",
        tool_name="speedaf.express.track.query",
        tool_status="success",
        pii_redacted=True,
        fact_evidence_present=True,
    )

    monkeypatch.setattr("app.services.speedaf.tracking_fact_source.lookup_speedaf_tracking_fact", lambda **_kwargs: primary)
    monkeypatch.setattr("app.services.speedaf.tracking_fact_source.lookup_speedaf_track_history_fact", lambda **_kwargs: history)

    fake_track_client = SimpleNamespace(config=SimpleNamespace(configured=True))
    result = lookup_speedaf_hybrid_tracking_fact(tracking_number="MK000179196R", track_client=fake_track_client)

    assert result is history
    assert result.fact_evidence_present is True


def test_track_history_empty_events_are_logged_as_successful_empty_fact(monkeypatch) -> None:
    calls = []

    class _Client:
        def query_history(self, mail_no: str) -> SpeedafTrackHistory:
            return SpeedafTrackHistory(mail_no=mail_no, events=(), raw_safe={})

    monkeypatch.setattr("app.services.speedaf.tracking_fact_source.record_tool_call", lambda **kwargs: calls.append(kwargs))

    result = lookup_speedaf_track_history_fact(
        tracking_number="CH020000129135",
        conversation_id=1,
        ticket_id=2,
        request_id="test-empty-track-history",
        client=_Client(),
    )

    assert result.ok is True
    assert result.tool_status == "success"
    assert result.fact_evidence_present is False
    assert calls[-1]["status"] == "success"
    assert calls[-1]["error_code"] is None
    assert calls[-1]["output_payload"]["fact_evidence_present"] is False
