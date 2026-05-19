from __future__ import annotations

from types import SimpleNamespace

from app.services.tracking_fact_schema import TrackingFactResult
from app.services import tracking_fact_service


def test_tracking_fact_service_routes_to_speedaf_source(monkeypatch):
    monkeypatch.setattr(
        tracking_fact_service,
        "settings",
        SimpleNamespace(
            webchat_tracking_fact_source="speedaf_api",
            webchat_tracking_fact_lookup_enabled=True,
            webchat_tracking_fact_timeout_seconds=8,
            openclaw_bridge_url="http://127.0.0.1:18792",
        ),
    )
    calls = []

    def fake_lookup_speedaf_tracking_fact(**kwargs):
        calls.append(kwargs)
        return TrackingFactResult(
            ok=True,
            tracking_number=kwargs["tracking_number"],
            source="speedaf_api.order_query",
            tool_name="speedaf.order.query",
            tool_status="success",
            pii_redacted=True,
            fact_evidence_present=True,
        )

    monkeypatch.setattr(tracking_fact_service, "lookup_speedaf_tracking_fact", fake_lookup_speedaf_tracking_fact)

    result = tracking_fact_service.lookup_tracking_fact(
        tracking_number="SPX123456789CH",
        caller_id="41000000000",
        conversation_id=11,
        ticket_id=22,
        request_id="req-1",
    )

    assert result.ok is True
    assert result.source == "speedaf_api.order_query"
    assert calls == [
        {
            "tracking_number": "SPX123456789CH",
            "caller_id": "41000000000",
            "conversation_id": 11,
            "ticket_id": 22,
            "request_id": "req-1",
        }
    ]


def test_tracking_fact_service_rejects_unknown_source(monkeypatch):
    monkeypatch.setattr(
        tracking_fact_service,
        "settings",
        SimpleNamespace(
            webchat_tracking_fact_source="unknown_source",
            webchat_tracking_fact_lookup_enabled=True,
            webchat_tracking_fact_timeout_seconds=8,
            openclaw_bridge_url="http://127.0.0.1:18792",
        ),
    )
    audit_calls = []
    monkeypatch.setattr(tracking_fact_service, "record_tool_call", lambda **kwargs: audit_calls.append(kwargs))

    result = tracking_fact_service.lookup_tracking_fact(tracking_number="SPX123456789CH")

    assert result.ok is False
    assert result.failure_reason == "unsupported_tracking_fact_source"
    assert audit_calls
    assert audit_calls[0]["provider"] == "openclaw_bridge"
