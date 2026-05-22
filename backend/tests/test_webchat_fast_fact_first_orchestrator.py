from __future__ import annotations

from app.api.webchat_fast import (
    _should_attempt_fact_first_lookup,
    _tracking_fact_forced_reply_payload,
)
from app.services.tracking_fact_schema import TrackingFactResult


def test_fact_first_reply_is_server_generated_and_grounded():
    result = TrackingFactResult(
        ok=True,
        tracking_number="CH020000008030",
        status="730",
        status_label="return delivered",
        tool_status="success",
        pii_redacted=True,
        fact_evidence_present=True,
        source="speedaf_api.order_query",
        tool_name="speedaf.order.query",
    )

    payload = _tracking_fact_forced_reply_payload(
        tracking_number="CH020000008030",
        result=result,
    )

    assert payload is not None
    assert payload["ok"] is True
    assert payload["ai_generated"] is False
    assert payload["reply_source"] == "server_tracking_fact"
    assert payload["intent"] == "tracking"
    assert payload["handoff_required"] is False
    assert payload["ticket_creation_queued"] is False
    assert "730" in payload["reply"]
    assert "return delivered" in payload["reply"]
    assert payload["tracking_fact"]["status"] == "730"
    assert payload["tracking_fact"]["status_label"] == "return delivered"


def test_fact_first_reply_refuses_untrusted_or_unredacted_fact():
    assert _tracking_fact_forced_reply_payload(
        tracking_number="CH020000008030",
        result=TrackingFactResult(
            ok=True,
            tracking_number="CH020000008030",
            status="730",
            status_label="return delivered",
            tool_status="success",
            pii_redacted=False,
            fact_evidence_present=True,
        ),
    ) is None

    assert _tracking_fact_forced_reply_payload(
        tracking_number="CH020000008030",
        result=TrackingFactResult(
            ok=True,
            tracking_number="CH020000008030",
            status="730",
            status_label="return delivered",
            tool_status="success",
            pii_redacted=True,
            fact_evidence_present=False,
        ),
    ) is None


def test_fact_first_lookup_requires_tracking_or_tracking_intent_with_caller():
    assert _should_attempt_fact_first_lookup(
        body="Where is CH020000008030?",
        tracking_number="CH020000008030",
        caller_id=None,
    ) is True

    assert _should_attempt_fact_first_lookup(
        body="Where is my parcel?",
        tracking_number=None,
        caller_id="+41790000000",
    ) is True

    assert _should_attempt_fact_first_lookup(
        body="What are your support hours?",
        tracking_number=None,
        caller_id="+41790000000",
    ) is False
