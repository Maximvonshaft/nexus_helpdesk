from __future__ import annotations

from typing import Any

from ..tracking_fact_schema import TrackingFactEvent, TrackingFactResult
from .redactor import redact_mapping
from .schemas import SpeedafOrderFact
from .status_map import safe_order_class_label, safe_order_status_label

SPEEDAF_TRACKING_FACT_SOURCE = "speedaf_api.order_query"
SPEEDAF_TRACKING_FACT_TOOL_NAME = "speedaf.order.query"


def _first(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


def order_fact_from_payload(payload: dict[str, Any], *, checked_at: str | None = None) -> SpeedafOrderFact:
    status = _first(payload, "status", "orderStatus", "waybillStatus")
    order_class = _first(payload, "orderClass", "order_class")
    return SpeedafOrderFact(
        waybill_code=_first(payload, "waybillCode", "waybill_code"),
        status=str(status) if status is not None else None,
        status_label=safe_order_status_label(str(status)) if status is not None else None,
        order_class=str(order_class) if order_class is not None else None,
        order_class_label=safe_order_class_label(str(order_class)) if order_class is not None else None,
        current_branch=_first(payload, "currentBranch", "current_branch", "branchName"),
        estimated_delivery_time=_first(payload, "estimatedDeliveryTime", "estimated_delivery_time", "estimateDeliveryTime"),
        checked_at=checked_at,
        raw_safe=redact_mapping(payload),
    )


def tracking_fact_from_order_fact(fact: SpeedafOrderFact) -> TrackingFactResult:
    # Keep current parcel status separate from order class. orderClass=2 is a
    # return-shipment classification, not a customer-visible explanation for an
    # unknown live status code such as status=4.
    description = fact.status_label or fact.status or None
    latest = TrackingFactEvent(
        event_time=fact.estimated_delivery_time,
        location=fact.current_branch,
        description=description,
    )
    return TrackingFactResult(
        ok=True,
        tracking_number=fact.waybill_code,
        status=fact.status,
        status_label=fact.status_label or fact.status,
        latest_event=latest if latest.is_present() else None,
        events_summary=[latest] if latest.is_present() else [],
        checked_at=fact.checked_at,
        source=SPEEDAF_TRACKING_FACT_SOURCE,
        tool_name=SPEEDAF_TRACKING_FACT_TOOL_NAME,
        tool_status="success",
        pii_redacted=True,
        fact_evidence_present=True,
    )
