from __future__ import annotations

from app.services.speedaf.adapter import SpeedafCoreAdapter
from app.services.speedaf.client import SpeedafMcpClient
from app.services.speedaf.schemas import SpeedafMcpConfig


class GuardFakeClient(SpeedafMcpClient):
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
        self.calls = []

    def post(self, path, data):
        self.calls.append((path, data))
        return self.normalize_response({"success": True, "data": {}}, status_code=200)


def test_order_query_skips_locally_without_caller_id():
    client = GuardFakeClient()
    adapter = SpeedafCoreAdapter(client)

    result = adapter.query_order_tracking_fact(waybill_code="CH120000005451", caller_id=None)

    assert result.ok is False
    assert result.tool_status == "skipped"
    assert result.failure_reason == "missing_caller_id"
    assert result.source == "speedaf_api.order_query"
    assert client.calls == []


def test_waybill_lookup_skips_locally_without_caller_id():
    client = GuardFakeClient()
    adapter = SpeedafCoreAdapter(client)

    result = adapter.query_waybills_by_caller(caller_id="", country_code="CH")

    assert result.ok is False
    assert result.failure_reason == "missing_caller_id"
    assert result.safe_summary["country_code"] == "CH"
    assert client.calls == []
