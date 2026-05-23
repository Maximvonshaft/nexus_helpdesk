from __future__ import annotations

from ...services.tracking_fact_schema import TrackingFactEvent, TrackingFactResult, safe_tracking_candidate
from ...utils.time import utc_now


def fake_tracking_fact_for_pilot(tracking_number: str | None) -> TrackingFactResult:
    return TrackingFactResult(
        ok=True,
        tracking_number=tracking_number,
        status="in_transit",
        status_label="In transit",
        latest_event=TrackingFactEvent(
            event_time=utc_now().isoformat(),
            location="redacted facility",
            description="Parcel scan recorded",
        ),
        checked_at=utc_now().isoformat(),
        source="pilot_fake_tracking",
        tool_name="pilot.fake_tracking.lookup",
        tool_status="success",
        pii_redacted=True,
        fact_evidence_present=True,
        safe_candidates=[safe_tracking_candidate(tracking_number)],
    )
