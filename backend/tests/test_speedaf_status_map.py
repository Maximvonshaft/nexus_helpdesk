from __future__ import annotations

from app.services.speedaf.status_map import (
    is_auto_work_order_type_allowed,
    safe_order_class_label,
    safe_order_status_label,
    safe_work_order_type_label,
)


def test_unknown_order_status_stays_conservative():
    assert safe_order_status_label("10") == "status:10"
    assert safe_order_status_label(None) is None


def test_order_class_label_is_conservative():
    assert safe_order_class_label("1") == "standard shipment"
    assert safe_order_class_label("UNKNOWN") == "order_class:UNKNOWN"


def test_only_urge_delivery_work_order_is_auto_allowed():
    assert is_auto_work_order_type_allowed("WT0103-05") is True
    assert is_auto_work_order_type_allowed("WT9999") is False
    assert safe_work_order_type_label("WT0103-05") == "delivery follow-up"
