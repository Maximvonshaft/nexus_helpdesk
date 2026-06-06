from __future__ import annotations

from app.services.speedaf.status_map import (
    is_auto_work_order_type_allowed,
    safe_order_class_label,
    safe_order_status_label,
    safe_work_order_type_label,
)


def test_official_mcp_order_status_labels():
    assert safe_order_status_label("10") == "pending pickup"
    assert safe_order_status_label("3750") == "in transit to destination country"
    assert safe_order_status_label("3751") == "received in destination country"
    assert safe_order_status_label("1") == "picked up"
    assert safe_order_status_label("2") == "in transit"
    assert safe_order_status_label("11") == "pending delivery"
    assert safe_order_status_label("18") == "ready for pickup"
    assert safe_order_status_label("4") == "out for delivery"
    assert safe_order_status_label("5") == "delivered"
    assert safe_order_status_label("730") == "return delivered"
    assert safe_order_status_label("-2") == "exception signed"
    assert safe_order_status_label(None) is None


def test_unknown_order_status_stays_conservative():
    assert safe_order_status_label("9999") == "Speedaf status code 9999"


def test_order_class_label_uses_official_mcp_spec():
    assert safe_order_class_label("1") == "local shipment"
    assert safe_order_class_label("2") == "international shipment"
    assert safe_order_class_label("UNKNOWN") == "order_class:UNKNOWN"


def test_only_urge_delivery_work_order_is_auto_allowed():
    assert is_auto_work_order_type_allowed("WT0103-05") is True
    assert is_auto_work_order_type_allowed("WT9999") is False
    assert safe_work_order_type_label("WT0103-05") == "delivery follow-up"
