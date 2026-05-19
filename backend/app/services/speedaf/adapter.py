from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..tracking_fact_schema import TrackingFactResult
from .client import SpeedafMcpClient, SpeedafMcpClientError
from .formatter import order_fact_from_payload, tracking_fact_from_order_fact
from .redactor import safe_caller_payload, safe_waybill_payload
from .schemas import SpeedafWaybillCandidate

ORDER_QUERY_PATH = "/open-api/mcp/order/query"
WAYBILL_BY_CALLER_PATH = "/open-api/mcp/order/waybillCode/query"


@dataclass(frozen=True)
class SpeedafWaybillLookupResult:
    ok: bool
    candidates: tuple[SpeedafWaybillCandidate, ...]
    safe_summary: dict[str, Any]
    failure_reason: str | None = None


class SpeedafCoreAdapter:
    """Business-level wrapper around Speedaf MCP read APIs."""

    def __init__(self, client: SpeedafMcpClient | None = None) -> None:
        self.client = client or SpeedafMcpClient()

    def query_waybills_by_caller(self, *, caller_id: str, country_code: str | None = None) -> SpeedafWaybillLookupResult:
        country = (country_code or self.client.config.country_code_default or "CH").strip().upper()
        try:
            response = self.client.post(WAYBILL_BY_CALLER_PATH, {"callerID": caller_id, "countryCode": country})
        except SpeedafMcpClientError as exc:
            return SpeedafWaybillLookupResult(ok=False, candidates=(), safe_summary=exc.safe_payload, failure_reason=exc.error.code)
        candidates = self._extract_waybill_candidates(response.data)
        safe_summary = {
            "tool": "speedaf.order.waybill_code.query",
            "ok": True,
            "count": len(candidates),
            **safe_caller_payload(caller_id),
            "country_code": country,
            "response": response.safe_summary.get("response"),
        }
        return SpeedafWaybillLookupResult(ok=True, candidates=tuple(candidates), safe_summary=safe_summary)

    def query_order_tracking_fact(self, *, waybill_code: str, caller_id: str | None = None) -> TrackingFactResult:
        payload: dict[str, Any] = {"waybillCode": waybill_code}
        if caller_id:
            payload["callerID"] = caller_id
        try:
            response = self.client.post(ORDER_QUERY_PATH, payload)
        except SpeedafMcpClientError as exc:
            return TrackingFactResult(
                ok=False,
                tracking_number=waybill_code,
                source="speedaf_api.order_query",
                tool_name="speedaf.order.query",
                tool_status="error",
                pii_redacted=True,
                fact_evidence_present=False,
                failure_reason=exc.error.code,
            )
        data = self._extract_order_payload(response.data)
        fact = order_fact_from_payload(data, checked_at=None)
        if fact.waybill_code is None:
            fact = type(fact)(
                waybill_code=waybill_code,
                status=fact.status,
                status_label=fact.status_label,
                order_class=fact.order_class,
                order_class_label=fact.order_class_label,
                current_branch=fact.current_branch,
                estimated_delivery_time=fact.estimated_delivery_time,
                checked_at=fact.checked_at,
                raw_safe=fact.raw_safe,
            )
        return tracking_fact_from_order_fact(fact)

    @staticmethod
    def _extract_waybill_candidates(data: Any) -> list[SpeedafWaybillCandidate]:
        source: Any = data
        if isinstance(source, dict):
            for key in ("list", "rows", "items", "records", "waybills", "waybillList"):
                if isinstance(source.get(key), list):
                    source = source.get(key)
                    break
            else:
                if source.get("waybillCode"):
                    source = [source]
        if not isinstance(source, list):
            return []
        candidates: list[SpeedafWaybillCandidate] = []
        for item in source:
            if isinstance(item, str):
                item = {"waybillCode": item, "waybillCodeSuffix": item[-4:]}
            if isinstance(item, dict):
                candidate = SpeedafWaybillCandidate.from_payload(item)
                if candidate:
                    candidates.append(candidate)
        return candidates

    @staticmethod
    def _extract_order_payload(data: Any) -> dict[str, Any]:
        if isinstance(data, dict):
            for key in ("order", "waybill", "detail", "result"):
                if isinstance(data.get(key), dict):
                    return data[key]
            return data
        return {"result": data}


def safe_query_summary(*, waybill_code: str | None = None, caller_id: str | None = None) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    if waybill_code:
        summary.update(safe_waybill_payload(waybill_code))
    if caller_id:
        summary.update(safe_caller_payload(caller_id))
    return summary
