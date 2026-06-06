from __future__ import annotations

from app.services.speedaf.client import SpeedafMcpClient
from app.services.speedaf.formatter import order_fact_from_payload, tracking_fact_from_order_fact
from app.services.speedaf.schemas import SpeedafMcpConfig
from app.services.speedaf.status_map import safe_order_class_label, safe_order_status_label


def _client() -> SpeedafMcpClient:
    return SpeedafMcpClient(
        SpeedafMcpConfig(
            enabled=True,
            base_url="https://uat-api.speedaf.com",
            app_code="test-app-code",
            secret_key="test-secret",
            timeout_seconds=8,
            country_code_default="CH",
            content_type="text/plain",
            data_mode="string",
            require_sign=False,
        )
    )


def test_speedaf_nested_business_error_is_not_flattened_to_http_200() -> None:
    response = _client().normalize_response(
        {
            "success": False,
            "error": {
                "code": "1140003",
                "message": "Waybill does not exist",
            },
            "data": None,
        },
        status_code=200,
        safe_request={"path": "/open-api/mcp/order/query"},
    )

    assert response.ok is False
    assert response.error is not None
    assert response.error.code == "1140003"
    assert response.error.message == "Waybill does not exist"
    assert response.error.http_status == 200


def test_official_status_code_is_used_without_order_class_confusion() -> None:
    assert safe_order_status_label("4") == "out for delivery"
    assert safe_order_class_label("2") == "international shipment"

    fact = order_fact_from_payload(
        {
            "waybillCode": "CH120000005451",
            "status": "4",
            "orderClass": 2,
            "currentBranch": "一级网点",
        }
    )
    tracking = tracking_fact_from_order_fact(fact)
    summary = tracking.prompt_summary()

    assert tracking.ok is True
    assert tracking.fact_evidence_present is True
    assert tracking.status == "4"
    assert tracking.status_label == "out for delivery"
    assert "out for delivery" in summary
    assert "return" not in summary.lower()
    assert "international" not in summary.lower()
