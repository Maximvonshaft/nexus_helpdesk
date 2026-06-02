from __future__ import annotations

import json

from app.api.webchat_fast import (
    _public_tracking_reference,
    _should_attempt_fact_first_lookup,
    _tracking_fact_evidence_trace,
    _tracking_fact_public_payload,
)
from app.services.tracking_fact_schema import TrackingFactResult


def test_fact_first_tracking_result_is_trusted_redacted_evidence_not_server_final_reply():
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

    fact_payload = _tracking_fact_public_payload(result)
    evidence_trace = _tracking_fact_evidence_trace(result, tracking_number="CH020000008030")
    public_ref = _public_tracking_reference("CH020000008030")

    assert fact_payload is not None
    assert fact_payload["tool_status"] == "success"
    assert fact_payload["fact_evidence_present"] is True
    assert fact_payload["pii_redacted"] is True
    assert fact_payload["status"] == "730"
    assert fact_payload["status_label"] == "return delivered"
    assert fact_payload["truth_trace"]["source"] == "speedaf_trusted_tracking_fact"
    assert evidence_trace["retrieval"] == "trusted_tracking_fact"
    assert evidence_trace["source"] == "speedaf_trusted_tracking_fact"
    assert evidence_trace["fact_evidence_present"] is True
    assert evidence_trace["raw_tracking_number_exposed"] is False
    assert public_ref["tracking_number"] is None
    assert public_ref["tracking_number_suffix"] == "008030"
    rendered = json.dumps({"fact": fact_payload, "trace": evidence_trace, "ref": public_ref}, ensure_ascii=False)
    assert "CH020000008030" not in rendered


def test_fact_first_trace_refuses_untrusted_or_unredacted_fact_as_evidence_present():
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
