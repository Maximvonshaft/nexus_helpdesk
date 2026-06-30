from __future__ import annotations

from types import SimpleNamespace

from app.services import tracking_fact_service
from app.services.tracking_fact_redactor import normalize_tracking_fact, sanitize_payload
from app.services.tracking_fact_schema import TrackingFactEvent, TrackingFactResult
from app.services.webchat_fact_gate import evaluate_webchat_fact_gate
from app.services.webchat_ai_service import _build_prompt


def test_tracking_number_extraction_skips_plain_words_and_accepts_waybill():
    assert tracking_fact_service.extract_tracking_number("hello world only") is None
    assert tracking_fact_service.extract_tracking_number("Please check PK120053679836") == "PK120053679836"
    assert tracking_fact_service.extract_tracking_number("waybill abcd12345678 now") == "ABCD12345678"


def test_tracking_number_extraction_handles_cjk_boundaries_and_dash_normalization():
    examples = {
        "CH020000006856": "CH020000006856",
        "CH020000006856这是我的订单号": "CH020000006856",
        "这是我的订单号CH020000006856": "CH020000006856",
        "单号：CH020000006856": "CH020000006856",
        "waybill CH020000006856": "CH020000006856",
        "CH-020000006856": "CH020000006856",
        "MA020001092814这是我的订单号": "MA020001092814",
    }
    for text, expected in examples.items():
        assert tracking_fact_service.extract_tracking_number(text) == expected
    assert tracking_fact_service.extract_tracking_number("订单 12345") is None
    assert tracking_fact_service.extract_tracking_number("Speedaf customer service") is None


def test_tracking_payload_redacts_pii_fields_and_raw_names():
    safe = sanitize_payload({
        "status": "delivered",
        "recipient_name": "John Smith",
        "phone": "+41 79 123 45 67",
        "email": "john@example.com",
        "latest_event": {
            "description": "Delivered to John Smith",
            "location": "PK FIN Center",
        },
    })

    assert safe["recipient_name_redacted"] == "J***h"
    assert safe["phone_redacted"] is True
    assert safe["email_redacted"] is True
    assert "recipient_name" not in safe
    assert safe["latest_event"]["location"] == "PK FIN Center"
    assert "John Smith" not in safe["latest_event"]["description"]
    assert "[redacted_name]" in safe["latest_event"]["description"]


def test_normalized_tracking_fact_is_sanitized_and_prompt_ready():
    fact = normalize_tracking_fact({
        "ok": True,
        "tracking_number": "PK120053679836",
        "status": "delivered",
        "status_label": "Delivered",
        "checked_at": "2026-05-03T20:00:00Z",
        "latest_event": {
            "event_time": "2026-03-24T17:40:55Z",
            "location": "PK FIN Center",
            "description": "Delivered to John Smith",
        },
        "pod": {"signed_by": "John Smith"},
    }, tracking_number="PK120053679836")

    assert fact.fact_evidence_present is True
    assert fact.pii_redacted is True
    assert fact.tool_name == "speedaf.order.query"
    metadata = fact.metadata_payload()
    assert metadata["fact_source"] == "speedaf_api.tracking_lookup"
    assert metadata["tracking_number_hash"].startswith("sha256:")
    prompt = fact.prompt_summary()
    assert "Trusted tracking fact" in prompt
    assert "Delivered" in prompt
    assert "John Smith" not in prompt
    assert "[redacted_name]" in prompt


def test_fact_gate_allows_delivered_only_with_fact_evidence():
    blocked = evaluate_webchat_fact_gate("Your parcel has been delivered.", fact_evidence_present=False)
    assert blocked.allowed is False
    assert blocked.reason in {"missing_business_or_tool_evidence", "missing_tracking_tool_result"}

    allowed = evaluate_webchat_fact_gate("Your parcel has been delivered.", fact_evidence_present=True)
    assert allowed.allowed is True
    assert allowed.fact_evidence_present is True


def test_tracking_lookup_disabled_does_not_call_provider(monkeypatch):
    monkeypatch.setattr(tracking_fact_service.settings, "webchat_tracking_fact_lookup_enabled", False)

    result = tracking_fact_service.lookup_tracking_fact(tracking_number="PK120053679836", conversation_id=1, ticket_id=2)
    assert result.fact_evidence_present is False
    assert result.failure_reason == "tracking_fact_lookup_disabled"


def test_legacy_bridge_tracking_source_is_unsupported(monkeypatch):
    monkeypatch.setattr(tracking_fact_service.settings, "webchat_tracking_fact_lookup_enabled", True)
    monkeypatch.setattr(tracking_fact_service.settings, "webchat_tracking_fact_source", "external_channel_bridge")
    monkeypatch.setattr(tracking_fact_service.settings, "webchat_tracking_fact_timeout_seconds", 8)

    result = tracking_fact_service.lookup_tracking_fact(
        tracking_number="PK120053679836",
        conversation_id=1,
        ticket_id=2,
        request_id="req-1",
    )

    assert result.fact_evidence_present is False
    assert result.pii_redacted is True
    assert result.failure_reason == "unsupported_tracking_fact_source"


def test_webchat_ai_prompt_includes_sanitized_tracking_fact_only():
    ticket = SimpleNamespace(ticket_no="T-1")
    conversation = SimpleNamespace(public_id="wc_test")
    visitor_message = SimpleNamespace(id=10, body="Where is PK120053679836?")
    fact = TrackingFactResult(
        ok=True,
        tracking_number="PK120053679836",
        status="delivered",
        status_label="Delivered",
        latest_event=TrackingFactEvent(
            event_time="2026-03-24T17:40:55Z",
            location="PK FIN Center",
            description="Delivered",
        ),
        checked_at="2026-05-03T20:00:00Z",
        tool_status="success",
        pii_redacted=True,
        fact_evidence_present=True,
    )

    prompt = _build_prompt(
        ticket=ticket,
        conversation=conversation,
        visitor_message=visitor_message,
        history_rows=[SimpleNamespace(direction="visitor", body="Where is PK120053679836?")],
        tracking_fact=fact,
    )

    assert "Trusted tracking fact" in prompt
    assert "Delivered" in prompt
    assert "Do not reveal recipient names" in prompt
    assert "raw tool output" in prompt


def test_tracking_fact_prompt_does_not_name_legacy_bridge():
    fact = TrackingFactResult(
        ok=True,
        tracking_number="PK120053679836",
        status_label="Delivered",
        checked_at="2026-05-03T20:00:00Z",
        pii_redacted=True,
        fact_evidence_present=True,
    )

    prompt = fact.prompt_summary()
    assert "ExternalChannel" not in prompt
    assert "Bridge" not in prompt
