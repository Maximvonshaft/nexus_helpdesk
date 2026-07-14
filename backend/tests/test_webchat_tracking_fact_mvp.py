from __future__ import annotations

from app.services import tracking_fact_service
from app.services.tracking_fact_schema import TrackingFactEvent, TrackingFactResult
from app.services.webchat_ai_service import _allows_history_tracking_lookup, _looks_like_service_policy_question
from app.services.webchat_fact_gate import evaluate_webchat_fact_gate


def test_tracking_number_extraction_skips_plain_words_and_accepts_waybill():
    assert tracking_fact_service.extract_tracking_number("hello world only") is None
    assert tracking_fact_service.extract_tracking_number("Please check PK120053679836") == "PK120053679836"
    assert tracking_fact_service.extract_tracking_number("waybill abcd12345678 now") == "ABCD12345678"
    assert tracking_fact_service.extract_tracking_number("hello keepalive api smoke 1783325193527") is None
    assert tracking_fact_service.extract_tracking_number("tracking 1783325193527") == "1783325193527"


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


def test_service_policy_question_does_not_inherit_history_tracking_lookup():
    assert _looks_like_service_policy_question("瑞士本地到本地现在支持寄送吗？") is True
    assert _allows_history_tracking_lookup("瑞士本地到本地现在支持寄送吗？") is False
    assert _allows_history_tracking_lookup("刚刚这个包裹如果收件人说没有收到怎么办？") is True


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


def test_tracking_fact_prompt_includes_sanitized_tracking_fact_only():
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

    prompt = fact.prompt_summary()

    assert "Trusted tracking fact" in prompt
    assert "Delivered" in prompt
    assert "PK120053679836" not in prompt
    assert "parcel ending 679836" in prompt
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
    assert "PK120053679836" not in prompt
    assert "parcel ending 679836" in prompt
