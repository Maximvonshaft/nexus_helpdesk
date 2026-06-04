from __future__ import annotations

import json

from app.api.webchat_fast import _public_tracking_reference, _tracking_fact_evidence_trace, _tracking_fact_public_payload
from app.services.tracking_fact_schema import TrackingFactResult


def test_tracking_fact_trace_returns_grounded_redacted_status_metadata():
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

    public_payload = _tracking_fact_public_payload(result)
    trace = _tracking_fact_evidence_trace(result, tracking_number="CH020000008030")
    public_ref = _public_tracking_reference("CH020000008030")

    assert public_payload is not None
    assert public_payload["fact_evidence_present"] is True
    assert public_payload["tool_status"] == "success"
    assert public_payload["status"] == "730"
    assert public_payload["status_label"] == "return delivered"
    assert public_payload["truth_trace"]["source"] == "speedaf_trusted_tracking_fact"
    assert public_payload["truth_trace"]["raw_tracking_number_exposed"] is False
    assert trace["retrieval"] == "trusted_tracking_fact"
    assert trace["source"] == "speedaf_trusted_tracking_fact"
    assert trace["fact_evidence_present"] is True
    assert trace["raw_tracking_number_exposed"] is False
    assert public_ref["tracking_number"] is None
    assert public_ref["tracking_number_suffix"] == "008030"
    assert public_ref["tracking_number_hash"]
    assert "CH020000008030" not in json.dumps({"payload": public_payload, "trace": trace, "ref": public_ref}, ensure_ascii=False)


def test_tracking_fact_trace_requires_evidence_and_redaction_for_fact_present():
    unredacted = TrackingFactResult(
        ok=True,
        tracking_number="CH020000008030",
        status="730",
        status_label="return delivered",
        tool_status="success",
        pii_redacted=False,
        fact_evidence_present=True,
    )
    missing_evidence = TrackingFactResult(
        ok=True,
        tracking_number="CH020000008030",
        status="730",
        status_label="return delivered",
        tool_status="success",
        pii_redacted=True,
        fact_evidence_present=False,
    )

    assert _tracking_fact_evidence_trace(unredacted, tracking_number="CH020000008030")["fact_evidence_present"] is False
    assert _tracking_fact_evidence_trace(missing_evidence, tracking_number="CH020000008030")["fact_evidence_present"] is False
    assert _tracking_fact_public_payload(unredacted)["truth_trace"]["raw_tracking_number_exposed"] is False
    assert _tracking_fact_public_payload(missing_evidence)["truth_trace"]["raw_tracking_number_exposed"] is False
