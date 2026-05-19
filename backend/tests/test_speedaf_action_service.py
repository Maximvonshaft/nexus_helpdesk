from __future__ import annotations

import os

import pytest

from app.services.speedaf.action_service import SpeedafActionDisabled, SpeedafActionService
from app.services.speedaf.client import SpeedafMcpClient
from app.services.speedaf.schemas import SpeedafMcpConfig


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
        self.calls = []

    def post(self, path, data):
        self.calls.append((path, data))
        return self.normalize_response({"success": True, "data": {"workOrderCode": "WO-1"}}, status_code=200)


def test_write_actions_are_disabled_by_default(monkeypatch):
    monkeypatch.delenv("SPEEDAF_WORK_ORDER_CREATE_ENABLED", raising=False)
    client = FakeClient()
    service = SpeedafActionService(client)

    with pytest.raises(SpeedafActionDisabled):
        service.create_work_order(
            waybill_code="SPX123456789CH",
            work_order_type="WT0103-05",
            description="delivery follow-up",
            caller_id="41000000000",
        )
    assert client.calls == []


def test_work_order_create_allowlist_and_redaction(monkeypatch):
    monkeypatch.setenv("SPEEDAF_WORK_ORDER_CREATE_ENABLED", "true")
    client = FakeClient()
    service = SpeedafActionService(client)

    result = service.create_work_order(
        waybill_code="SPX123456789CH",
        work_order_type="WT0103-05",
        description="delivery follow-up",
        caller_id="41000000000",
    )

    assert result.ok is True
    assert result.external_id == "WO-1"
    assert client.calls[0][0].endswith("/workOrder/create")
    safe_text = str(result.safe_payload)
    assert "41000000000" not in safe_text


def test_non_allowlisted_work_order_is_blocked(monkeypatch):
    monkeypatch.setenv("SPEEDAF_WORK_ORDER_CREATE_ENABLED", "true")
    client = FakeClient()
    service = SpeedafActionService(client)

    with pytest.raises(SpeedafActionDisabled):
        service.create_work_order(
            waybill_code="SPX123456789CH",
            work_order_type="WT9999",
            description="not allowed",
            caller_id="41000000000",
        )
    assert client.calls == []
