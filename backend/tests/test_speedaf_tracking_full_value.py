from __future__ import annotations

from app.services.speedaf.schemas import SpeedafWaybillCandidate
from app.services.speedaf.tracking_fact_source import lookup_speedaf_tracking_fact
from app.services.tracking_fact_schema import TrackingFactResult


class FakeLookup:
    def __init__(self, ok=True, candidates=(), failure_reason=None):
        self.ok = ok
        self.candidates = tuple(candidates)
        self.failure_reason = failure_reason


class FakeAdapter:
    def __init__(self, candidates):
        self.candidates = candidates
        self.lookup_calls = []
        self.order_calls = []

    def query_waybills_by_caller(self, *, caller_id: str, country_code: str | None = None):
        self.lookup_calls.append((caller_id, country_code))
        return FakeLookup(ok=True, candidates=self.candidates)

    def query_order_tracking_fact(self, *, waybill_code: str, caller_id: str | None = None):
        self.order_calls.append((waybill_code, caller_id))
        return TrackingFactResult(ok=True, tracking_number=waybill_code, status="5", status_label="delivered", tool_status="success", pii_redacted=True, fact_evidence_present=True, source="speedaf_api.order_query", tool_name="speedaf.order.query")


def test_no_tracking_and_one_caller_candidate_auto_queries_order(monkeypatch):
    monkeypatch.setattr("app.services.speedaf.tracking_fact_source.record_tool_call", lambda **kwargs: None)
    adapter = FakeAdapter([SpeedafWaybillCandidate(waybill_code="MA020001092814", suffix="2814")])
    result = lookup_speedaf_tracking_fact(tracking_number=None, caller_id="41000000000", country_code="CH", adapter=adapter)
    assert result.ok is True
    assert result.tracking_number == "MA020001092814"
    assert adapter.lookup_calls == [("41000000000", "CH")]
    assert adapter.order_calls == [("MA020001092814", "41000000000")]


def test_no_tracking_and_multiple_candidates_returns_safe_selection(monkeypatch):
    monkeypatch.setattr("app.services.speedaf.tracking_fact_source.record_tool_call", lambda **kwargs: None)
    adapter = FakeAdapter([
        SpeedafWaybillCandidate(waybill_code="MA020001092814", suffix="2814"),
        SpeedafWaybillCandidate(waybill_code="MA020001099999", suffix="9999"),
    ])
    result = lookup_speedaf_tracking_fact(tracking_number="", caller_id="41000000000", country_code="CH", adapter=adapter)
    payload = result.metadata_payload()
    assert result.ok is False
    assert result.failure_reason == "multiple_waybill_candidates"
    assert result.tool_status == "needs_customer_selection"
    assert [item["waybill_suffix"] for item in payload["safe_candidates"]] == ["2814", "9999"]
    rendered = str(payload)
    assert "MA020001092814" not in rendered
    assert "MA020001099999" not in rendered
    assert adapter.order_calls == []


def test_no_tracking_and_no_caller_stays_missing_tracking():
    result = lookup_speedaf_tracking_fact(tracking_number=None, caller_id=None, adapter=FakeAdapter([]))
    assert result.failure_reason == "missing_tracking_number"
