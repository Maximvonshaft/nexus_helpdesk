from __future__ import annotations

from app.api.webchat_fast import _tracking_fact_forced_reply_payload
from app.services.tracking_fact_schema import TrackingFactResult


def test_tracking_fact_forced_reply_payload_returns_grounded_status():
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
    assert payload["tracking_number"] is None
    assert payload["tracking_number_suffix"] == "008030"
    assert payload["tracking_number_hash"]
    assert payload["evidence_trace"]["retrieval"] == "trusted_tracking_fact"
    assert payload["evidence_trace"]["source"] == "speedaf_trusted_tracking_fact"
    assert payload["evidence_trace"]["fact_evidence_present"] is True
    assert payload["evidence_trace"]["raw_tracking_number_exposed"] is False
    assert payload["handoff_required"] is False
    assert payload["ticket_creation_queued"] is False
    assert "730" in payload["reply"]
    assert "return delivered" in payload["reply"]
    assert "provide" not in payload["reply"].lower()
    assert "cannot verify" not in payload["reply"].lower()


def test_tracking_fact_forced_reply_payload_requires_evidence_and_redaction():
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
