from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from app.services import tracking_fact_service
from app.services.tracking_fact_redactor import normalize_tracking_fact, sanitize_payload
from app.services.tracking_fact_schema import TrackingFactEvent, TrackingFactResult
from app.services.webchat_fact_gate import evaluate_webchat_fact_gate
from app.services.webchat_ai_service import _build_prompt


class _Resp:
    def __init__(self, payload: dict):
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_tracking_number_extraction_skips_plain_words_and_accepts_waybill():
    assert tracking_fact_service.extract_tracking_number("hello world only") is None
    assert tracking_fact_service.extract_tracking_number("Please check PK120053679836") == "PK120053679836"
    assert tracking_fact_service.extract_tracking_number("waybill abcd12345678 now") == "ABCD12345678"


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
            "description": "Delivered",
        },
        "pod": {"signed_by": "John Smith"},
    }, tracking_number="PK120053679836")

    assert fact.fact_evidence_present is True
    assert fact.pii_redacted is True
    assert fact.tool_name == "speedaf_lookup"
    metadata = fact.metadata_payload()
    assert metadata["fact_source"] == "openclaw_bridge.speedaf_lookup"
    assert metadata["tracking_number_hash"].startswith("sha256:")
    prompt = fact.prompt_summary()
    assert "Trusted tracking fact" in prompt
    assert "Delivered" in prompt
    assert "John Smith" not in prompt


def test_fact_gate_allows_delivered_only_with_fact_evidence():
    blocked = evaluate_webchat_fact_gate("Your parcel has been delivered.", fact_evidence_present=False)
    assert blocked.allowed is False
    assert blocked.reason in {"missing_business_or_tool_evidence", "missing_tracking_tool_result"}

    allowed = evaluate_webchat_fact_gate("Your parcel has been delivered.", fact_evidence_present=True)
    assert allowed.allowed is True
    assert allowed.fact_evidence_present is True


def test_tracking_lookup_disabled_does_not_call_bridge(monkeypatch):
    monkeypatch.setattr(tracking_fact_service.settings, "webchat_tracking_fact_lookup_enabled", False)

    def fail_urlopen(*args, **kwargs):
        raise AssertionError("bridge should not be called when feature flag is off")

    monkeypatch.setattr(tracking_fact_service.urllib.request, "urlopen", fail_urlopen)
    result = tracking_fact_service.lookup_tracking_fact(tracking_number="PK120053679836", conversation_id=1, ticket_id=2)
    assert result.fact_evidence_present is False
    assert result.failure_reason == "tracking_fact_lookup_disabled"


def test_tracking_lookup_success_uses_bridge_and_redacts(monkeypatch):
    monkeypatch.setattr(tracking_fact_service.settings, "webchat_tracking_fact_lookup_enabled", True)
    monkeypatch.setattr(tracking_fact_service.settings, "webchat_tracking_fact_source", "openclaw_bridge")
    monkeypatch.setattr(tracking_fact_service.settings, "webchat_tracking_fact_timeout_seconds", 8)
    monkeypatch.setattr(tracking_fact_service.settings, "openclaw_bridge_url", "http://bridge.test")

    calls = []

    def fake_urlopen(req, timeout=0):
        calls.append((req.full_url, json.loads(req.data.decode("utf-8")), timeout))
        return _Resp({
            "ok": True,
            "tool_status": "success",
            "result": {
                "tracking_number": "PK120053679836",
                "status": "delivered",
                "status_label": "Delivered",
                "checked_at": "2026-05-03T20:00:00Z",
                "latest_event": {
                    "event_time": "2026-03-24T17:40:55Z",
                    "location": "PK FIN Center",
                    "description": "Delivered",
                },
                "pod": {"signed_by": "John Smith"},
            },
        })

    monkeypatch.setattr(tracking_fact_service.urllib.request, "urlopen", fake_urlopen)
    result = tracking_fact_service.lookup_tracking_fact(
        tracking_number="PK120053679836",
        conversation_id=1,
        ticket_id=2,
        request_id="req-1",
    )

    assert calls[0][0] == "http://bridge.test/tools/speedaf_lookup"
    assert calls[0][1]["tracking_number"] == "PK120053679836"
    assert calls[0][1]["source"] == "nexus_webchat"
    assert calls[0][2] == 8
    assert result.fact_evidence_present is True
    assert result.pii_redacted is True
    assert "John Smith" not in result.prompt_summary()


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


def test_bridge_tracking_endpoint_is_read_only_and_feature_gated():
    bridge_script = Path(__file__).resolve().parents[1] / "scripts" / "openclaw_bridge_server.js"
    source = bridge_script.read_text(encoding="utf-8")
    lookup_block = source.split("async lookupSpeedaf(payload) {", 1)[1].split("\n  pollEvents(payload)", 1)[0]
    send_block = source.split("async sendMessage(payload) {", 1)[1].split("\n  async listConversations", 1)[0]

    assert "OPENCLAW_BRIDGE_TRACKING_LOOKUP_ENABLED" in source
    assert "SPEEDAF_LOOKUP_PATH = '/tools/speedaf_lookup'" in source
    assert "bridge_tracking_lookup_disabled" in lookup_block
    assert "this.config.allowWrites" not in lookup_block
    assert "if (!this.config.allowWrites) throw new Error('bridge_writes_disabled');" in send_block
