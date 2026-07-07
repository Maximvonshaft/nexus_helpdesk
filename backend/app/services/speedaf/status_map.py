from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CodeLabel:
    code: str
    label: str
    customer_label: str
    description: str | None = None
    handling_hint: str | None = None
    needs_human_review: bool = False


# Speedaf AI customer-service MCP spec, attachment 1: status codes.
# Unknown values must still be surfaced as safe codes rather than invented
# operational meanings.
ORDER_STATUS_LABELS: dict[str, CodeLabel] = {
    "10": CodeLabel("10", "pending_pickup", "pending pickup", "Order created and waiting for pickup."),
    "150": CodeLabel("150", "in_origin_warehouse", "in origin warehouse", "Parcel has entered the origin warehouse or sorting center."),
    "181": CodeLabel("181", "consolidated", "consolidated for line-haul transport", "Parcel has been consolidated and is waiting for line-haul processing."),
    "190": CodeLabel("190", "departed_warehouse", "departed warehouse", "Parcel has left the warehouse or sorting center."),
    "191": CodeLabel("191", "handover_completed", "handover completed", "Origin-side handover is complete and the parcel is ready for line-haul transport."),
    "220": CodeLabel("220", "flight_departed", "flight departed", "Parcel is on the line-haul flight."),
    "230": CodeLabel("230", "flight_arrived", "flight arrived", "Parcel has arrived in the destination country or region."),
    "360": CodeLabel("360", "customs_clearance_in_progress", "customs clearance in progress", "Parcel is being processed by customs."),
    "370": CodeLabel("370", "customs_cleared", "customs cleared", "Customs clearance is complete."),
    "375": CodeLabel("375", "arrived_at_destination_hub", "arrived at destination hub", "Parcel has arrived at the destination hub."),
    "3750": CodeLabel("3750", "in_transit_to_destination_country", "in transit to destination country", "Parcel is moving toward the destination country."),
    "3751": CodeLabel("3751", "received_in_destination_country", "received in destination country", "Parcel has reached the destination country and was received by the local network."),
    "2": CodeLabel("2", "in_transit", "in transit", "Parcel is moving through the logistics network."),
    "11": CodeLabel("11", "pending_delivery", "pending delivery", "Parcel has been assigned for delivery preparation.", "The customer should keep their phone reachable."),
    "730": CodeLabel("730", "return_delivered", "return delivered", "Returned parcel has been delivered back to the sender."),
    "-2": CodeLabel("-2", "exception_signed", "exception signed", "Delivery signature has an exception.", needs_human_review=True),
    "1": CodeLabel("1", "picked_up", "picked up", "Parcel was received or scanned at the current station."),
    "4": CodeLabel("4", "out_for_delivery", "out for delivery", "Courier is delivering the parcel.", "The customer should keep their phone reachable."),
    "5": CodeLabel("5", "delivered", "delivered", "Parcel was delivered successfully."),
    "18": CodeLabel("18", "ready_for_pickup", "ready for pickup", "Parcel is available at a pickup point."),
    "401": CodeLabel("401", "customs_exception", "customs exception", "Parcel has a customs-clearance exception.", needs_human_review=True),
}

# Speedaf AI customer-service MCP spec, attachment 2: orderClass.
ORDER_CLASS_LABELS: dict[str, CodeLabel] = {
    "1": CodeLabel("1", "local", "local shipment"),
    "2": CodeLabel("2", "international", "international shipment"),
}

WORK_ORDER_TYPE_LABELS: dict[str, CodeLabel] = {
    "WT0103-05": CodeLabel("WT0103-05", "urge_delivery", "delivery follow-up"),
}

CANCEL_REASON_LABELS: dict[str, CodeLabel] = {
    "CC01": CodeLabel("CC01", "delivery_too_slow", "delivery is taking too long"),
    "CC02": CodeLabel("CC02", "courier_attitude", "courier service issue"),
    "CC03": CodeLabel("CC03", "inspection_not_supported", "inspection before signing is not supported"),
    "CC04": CodeLabel("CC04", "partial_sign_not_supported", "partial signing is not supported"),
    "CC05": CodeLabel("CC05", "other", "other reason"),
}

ACTION_STATUS_LABELS: dict[str, CodeLabel] = {
    "SUCCESS": CodeLabel("SUCCESS", "success", "completed"),
    "FAILED": CodeLabel("FAILED", "failed", "failed"),
}

TERMINAL_CANCEL_STATUS_CODES = {"5", "730", "-2"}
TERMINAL_CANCEL_STATUS_LABELS = {
    "delivered",
    "return delivered",
    "exception signed",
}

CUSTOMS_EXCEPTION_TERMS = (
    "customs exception",
    "clearance exception",
    "held by customs",
    "customs hold",
    "清关异常",
    "海关扣留",
    "海关拦截",
)

DANGER_TERMS = (
    "exception",
    "failed",
    "failure",
    "hold",
    "held",
    "rejected",
    "abnormal",
    "detained",
    "seized",
    "returned",
    "returning",
    "intercept",
    "unable to contact",
    "address issue",
    "delivery failed",
    "异常",
    "失败",
    "扣关",
    "退回",
    "退件",
    "拒收",
    "拦截",
    "查验异常",
    "地址异常",
    "联系不上",
    "派送失败",
)


def safe_label(mapping: dict[str, CodeLabel], code: str | None, *, unknown_prefix: str = "unknown") -> str | None:
    cleaned = (code or "").strip()
    if not cleaned:
        return None
    item = mapping.get(cleaned)
    if item:
        return item.customer_label
    return f"{unknown_prefix}:{cleaned}"


def safe_order_status_label(status: str | None) -> str | None:
    cleaned = (status or "").strip()
    if not cleaned:
        return None
    item = ORDER_STATUS_LABELS.get(cleaned)
    if item:
        return item.customer_label
    return f"Speedaf status code {cleaned}"


def order_status_context(status: str | None) -> dict[str, str | bool]:
    cleaned = (status or "").strip()
    if not cleaned:
        return {}
    item = ORDER_STATUS_LABELS.get(cleaned)
    if not item:
        return {
            "code": cleaned,
            "label": f"Speedaf status code {cleaned}",
            "description": "Speedaf returned a status code that is not mapped in Nexus.",
            "needs_human_review": True,
        }
    payload: dict[str, str | bool] = {
        "code": item.code,
        "label": item.customer_label,
    }
    if item.description:
        payload["description"] = item.description
    if item.handling_hint:
        payload["handling_hint"] = item.handling_hint
    if item.needs_human_review:
        payload["needs_human_review"] = True
    return payload


def tracking_event_needs_human_review(event: Any) -> bool:
    action = str(getattr(event, "action", None) or _mapping_value(event, "action") or _mapping_value(event, "status") or "").strip()
    if action in {"401", "-2"}:
        return True
    haystack = " ".join(
        str(
            getattr(event, attr, None)
            or _mapping_value(event, key)
            or ""
        ).lower()
        for attr, key in (
            ("action_name", "actionName"),
            ("message", "message"),
            ("msg_loc", "msgLoc"),
            ("msg_eng", "msgEng"),
            ("msg_sh", "msgSh"),
            ("sub_action", "subAction"),
        )
    )
    if any(term in haystack for term in CUSTOMS_EXCEPTION_TERMS):
        return True
    return any(term in haystack for term in DANGER_TERMS)


def _mapping_value(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return None


def safe_order_class_label(order_class: str | None) -> str | None:
    return safe_label(ORDER_CLASS_LABELS, order_class, unknown_prefix="order_class")


def safe_work_order_type_label(work_order_type: str | None) -> str | None:
    return safe_label(WORK_ORDER_TYPE_LABELS, work_order_type, unknown_prefix="work_order")


def safe_cancel_reason_label(reason_code: str | None) -> str | None:
    return safe_label(CANCEL_REASON_LABELS, reason_code, unknown_prefix="cancel_reason")


def is_cancel_reason_code_allowed(reason_code: str | None) -> bool:
    return (reason_code or "").strip().upper() in CANCEL_REASON_LABELS


def is_cancel_terminal_status(status: str | None, status_label: str | None = None) -> bool:
    cleaned_status = (status or "").strip()
    if cleaned_status in TERMINAL_CANCEL_STATUS_CODES:
        return True
    cleaned_label = (status_label or "").strip().lower()
    return bool(cleaned_label and cleaned_label in {item.lower() for item in TERMINAL_CANCEL_STATUS_LABELS})


def is_auto_work_order_type_allowed(work_order_type: str | None) -> bool:
    return (work_order_type or "").strip() == "WT0103-05"
