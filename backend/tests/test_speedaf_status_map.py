from __future__ import annotations

from app.services.speedaf.status_map import (
    ORDER_STATUS_LABELS,
    is_auto_work_order_type_allowed,
    order_status_context,
    safe_order_class_label,
    safe_order_status_label,
    safe_work_order_type_label,
    tracking_event_needs_human_review,
)

SUPPORT_AGENT_STATUS_CODES = {
    "-2",
    "1",
    "10",
    "11",
    "150",
    "18",
    "181",
    "190",
    "191",
    "2",
    "220",
    "230",
    "360",
    "370",
    "375",
    "3750",
    "3751",
    "4",
    "401",
    "5",
    "730",
}


def test_status_map_covers_support_agent_codes():
    assert SUPPORT_AGENT_STATUS_CODES <= set(ORDER_STATUS_LABELS)


def test_official_mcp_order_status_labels():
    assert safe_order_status_label("10") == "pending pickup"
    assert safe_order_status_label("150") == "in origin warehouse"
    assert safe_order_status_label("181") == "consolidated for line-haul transport"
    assert safe_order_status_label("190") == "departed warehouse"
    assert safe_order_status_label("191") == "handover completed"
    assert safe_order_status_label("220") == "flight departed"
    assert safe_order_status_label("230") == "flight arrived"
    assert safe_order_status_label("360") == "customs clearance in progress"
    assert safe_order_status_label("370") == "customs cleared"
    assert safe_order_status_label("375") == "arrived at destination hub"
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
    assert safe_order_status_label("401") == "customs exception"
    assert safe_order_status_label(None) is None


def test_unknown_order_status_stays_conservative():
    assert safe_order_status_label("9999") == "Speedaf status code 9999"
    assert order_status_context("9999") == {
        "code": "9999",
        "label": "Speedaf status code 9999",
        "description": "Speedaf returned a status code that is not mapped in Nexus.",
        "needs_human_review": True,
    }


def test_status_context_absorbs_support_agent_meaning_without_customer_template():
    context = order_status_context("401")
    assert context["label"] == "customs exception"
    assert context["description"] == "Parcel has a customs-clearance exception."
    assert context["needs_human_review"] is True

    context = order_status_context("4")
    assert context["label"] == "out for delivery"
    assert context["handling_hint"] == "The customer should keep their phone reachable."


def test_support_agent_exception_terms_mark_human_review_without_templates():
    event = {
        "action": "4",
        "msgEng": "Delivery failed because the recipient could not be contacted",
    }

    assert tracking_event_needs_human_review(event) is True
    assert tracking_event_needs_human_review({"action": "4", "msgEng": "Out for delivery"}) is False


def test_order_class_label_uses_official_mcp_spec():
    assert safe_order_class_label("1") == "local shipment"
    assert safe_order_class_label("2") == "international shipment"
    assert safe_order_class_label("UNKNOWN") == "order_class:UNKNOWN"


def test_only_urge_delivery_work_order_is_auto_allowed():
    assert is_auto_work_order_type_allowed("WT0103-05") is True
    assert is_auto_work_order_type_allowed("WT9999") is False
    assert safe_work_order_type_label("WT0103-05") == "delivery follow-up"
