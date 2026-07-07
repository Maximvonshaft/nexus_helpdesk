from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SpeedafErrorMeaning:
    raw_code: str | None
    kind: str
    customer_safe_summary: str
    retryable: bool = False
    needs_customer_confirmation: bool = False
    needs_human_review: bool = False


_CODE_MEANINGS: dict[str, SpeedafErrorMeaning] = {
    "1140003": SpeedafErrorMeaning(
        raw_code="1140003",
        kind="tracking_lookup_no_match",
        customer_safe_summary="No matching shipment was returned for the provided waybill and contact context.",
        retryable=False,
        needs_customer_confirmation=True,
    ),
    "1140004": SpeedafErrorMeaning(
        raw_code="1140004",
        kind="shipment_state_blocks_action",
        customer_safe_summary="The current shipment state does not allow the requested automated action.",
        retryable=False,
        needs_human_review=True,
    ),
    "1200002": SpeedafErrorMeaning(
        raw_code="1200002",
        kind="speedaf_system_temporarily_unavailable",
        customer_safe_summary="Speedaf could not complete the request because the backend returned a system error.",
        retryable=True,
    ),
    "3000000": SpeedafErrorMeaning(
        raw_code="3000000",
        kind="operation_not_supported_for_current_state",
        customer_safe_summary="The requested operation is not supported for the shipment's current state.",
        retryable=False,
        needs_human_review=True,
    ),
    "timeout": SpeedafErrorMeaning(
        raw_code="timeout",
        kind="speedaf_timeout",
        customer_safe_summary="Speedaf did not respond before the timeout.",
        retryable=True,
    ),
    "http_error": SpeedafErrorMeaning(
        raw_code="http_error",
        kind="speedaf_http_error",
        customer_safe_summary="Speedaf returned a transport error.",
        retryable=True,
    ),
    "speedaf_mcp_not_configured": SpeedafErrorMeaning(
        raw_code="speedaf_mcp_not_configured",
        kind="speedaf_integration_not_configured",
        customer_safe_summary="The Speedaf integration is not configured.",
        retryable=False,
    ),
    "sign_rule_not_configured": SpeedafErrorMeaning(
        raw_code="sign_rule_not_configured",
        kind="speedaf_signature_rule_not_configured",
        customer_safe_summary="The Speedaf signing rule is not configured.",
        retryable=False,
    ),
    "missing_caller_id": SpeedafErrorMeaning(
        raw_code="missing_caller_id",
        kind="missing_caller_id",
        customer_safe_summary="A caller/contact value is required before Speedaf can verify this shipment.",
        retryable=False,
        needs_customer_confirmation=True,
    ),
    "missing_tracking_number": SpeedafErrorMeaning(
        raw_code="missing_tracking_number",
        kind="missing_tracking_number",
        customer_safe_summary="A tracking number is required before Speedaf can verify this shipment.",
        retryable=False,
        needs_customer_confirmation=True,
    ),
}


def normalize_speedaf_error(code: Any, *, message: str | None = None, retryable: bool | None = None) -> SpeedafErrorMeaning:
    cleaned = str(code or "").strip()
    if not cleaned and message:
        lowered = message.lower()
        if "waybill" in lowered and ("not exist" in lowered or "does not exist" in lowered):
            cleaned = "1140003"
        elif "timeout" in lowered:
            cleaned = "timeout"
    known = _CODE_MEANINGS.get(cleaned)
    if known:
        if retryable is None or retryable == known.retryable:
            return known
        return SpeedafErrorMeaning(
            raw_code=known.raw_code,
            kind=known.kind,
            customer_safe_summary=known.customer_safe_summary,
            retryable=retryable,
            needs_customer_confirmation=known.needs_customer_confirmation,
            needs_human_review=known.needs_human_review,
        )
    if cleaned.startswith("http_"):
        return SpeedafErrorMeaning(
            raw_code=cleaned,
            kind="speedaf_http_error",
            customer_safe_summary="Speedaf returned a transport error.",
            retryable=retryable if retryable is not None else False,
        )
    return SpeedafErrorMeaning(
        raw_code=cleaned or None,
        kind="speedaf_unknown_error",
        customer_safe_summary="Speedaf did not return a recognized successful result.",
        retryable=retryable if retryable is not None else False,
    )
