from __future__ import annotations

import json

from app.services.speedaf.schemas import SpeedafMcpConfig
from app.services.speedaf.client import SpeedafMcpClient
from app.services.speedaf.tracking_fact_source import lookup_speedaf_tracking_fact


class FakeClient(SpeedafMcpClient):
    def __init__(self):
        super().__init__(SpeedafMcpConfig(
            enabled=True,
            base_url="https://uat-api.speedaf.com",
            app_code="test-app-code",
            secret_key=None,
            timeout_seconds=8,
            country_code_default="CH",
            content_type="text/plain",
            data_mode="string",
            require_sign=False,
        ))

    def post(self, path, data):
        return self.normalize_response(
            {
                "success": True,
                "data": {
                    "waybillCode": data.get("waybillCode"),
                    "status": "10",
                    "orderClass": "1",
                    "currentBranch": "Zurich Branch",
                    "estimatedDeliveryTime": "2026-05-20 12:00:00",
                    "acceptMobile": "41000000000",
                    "acceptAddress": "Private Address 1",
                },
            },
            status_code=200,
        )


def test_speedaf_tracking_fact_is_redacted_and_evidence_present(monkeypatch):
    from app.services.speedaf.adapter import SpeedafCoreAdapter

    calls = []

    def fake_record_tool_call(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("app.services.speedaf.tracking_fact_source.record_tool_call", fake_record_tool_call)
    result = lookup_speedaf_tracking_fact(
        tracking_number="SPX123456789CH",
        caller_id="41000000000",
        conversation_id=1,
        request_id="req-1",
        adapter=SpeedafCoreAdapter(FakeClient()),
    )

    assert result.ok is True
    assert result.fact_evidence_present is True
    assert result.pii_redacted is True
    summary = result.prompt_summary()
    assert "Private Address" not in summary
    assert "41000000000" not in summary
    assert "Trusted tracking fact" in summary

    assert calls
    audit_text = json.dumps(calls[0], ensure_ascii=False, default=str)
    assert "Private Address" not in audit_text
    assert "41000000000" not in audit_text


def test_speedaf_tracking_fact_missing_number_skips():
    result = lookup_speedaf_tracking_fact(tracking_number=None)
    assert result.ok is False
    assert result.failure_reason == "missing_tracking_number"
