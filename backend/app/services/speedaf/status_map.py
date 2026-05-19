from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CodeLabel:
    code: str
    label: str
    customer_label: str


# Conservative labels. Unknown values must be surfaced as safe codes rather than
# invented operational meanings.
ORDER_CLASS_LABELS: dict[str, CodeLabel] = {
    "1": CodeLabel("1", "standard", "standard shipment"),
    "2": CodeLabel("2", "return", "return shipment"),
    "3": CodeLabel("3", "pickup", "pickup shipment"),
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
    # The Speedaf document does not provide a complete status dictionary. Keep
    # labels conservative until Speedaf confirms the official code table.
    return f"status:{cleaned}"


def safe_order_class_label(order_class: str | None) -> str | None:
    return safe_label(ORDER_CLASS_LABELS, order_class, unknown_prefix="order_class")


def safe_work_order_type_label(work_order_type: str | None) -> str | None:
    return safe_label(WORK_ORDER_TYPE_LABELS, work_order_type, unknown_prefix="work_order")


def is_auto_work_order_type_allowed(work_order_type: str | None) -> bool:
    return (work_order_type or "").strip() == "WT0103-05"
