from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .client import SpeedafMcpClient, SpeedafMcpClientError
from .redactor import redact_mapping, safe_caller_payload, safe_waybill_payload
from .schemas import SpeedafWorkOrderResult
from .status_map import is_auto_work_order_type_allowed

WORK_ORDER_CREATE_PATH = "/open-api/mcp/workOrder/create"
ORDER_CANCEL_PATH = "/open-api/mcp/order/cancel"
UPDATE_ADDRESS_PATH = "/open-api/mcp/order/updateAddress"
VOICE_CALLBACK_PATH = "/open-api/mcp/callData/voice/callBack"


def _enabled(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class SpeedafActionDisabled(RuntimeError):
    pass


@dataclass(frozen=True)
class SpeedafActionResult:
    ok: bool
    action_type: str
    status: str
    safe_payload: dict[str, Any]
    error_code: str | None = None
    error_message: str | None = None


class SpeedafActionService:
    """Feature-flagged write/system operations for Speedaf MCP.

    This layer is intentionally backend-only and should be called from
    background jobs or explicit backend confirmation flows, never directly from
    LLM output.
    """

    def __init__(self, client: SpeedafMcpClient | None = None) -> None:
        self.client = client or SpeedafMcpClient()

    def create_work_order(
        self,
        *,
        waybill_code: str,
        work_order_type: str,
        description: str,
        caller_id: str,
    ) -> SpeedafWorkOrderResult:
        if not _enabled("SPEEDAF_WORK_ORDER_CREATE_ENABLED", False):
            raise SpeedafActionDisabled("speedaf_work_order_create_disabled")
        if not is_auto_work_order_type_allowed(work_order_type):
            raise SpeedafActionDisabled("speedaf_work_order_type_not_allowed")
        payload = {
            "waybillCode": waybill_code,
            "workOrderType": work_order_type,
            "description": description[:1000],
            "callerID": caller_id,
        }
        try:
            response = self.client.post(WORK_ORDER_CREATE_PATH, payload)
        except SpeedafMcpClientError as exc:
            return SpeedafWorkOrderResult(
                ok=False,
                status="failed",
                error_code=exc.error.code,
                error_message=exc.error.message,
                safe_payload=exc.safe_payload,
            )
        data = response.data if isinstance(response.data, dict) else {}
        external_id = str(data.get("workOrderCode") or data.get("workOrderId") or data.get("id") or "").strip() or None
        return SpeedafWorkOrderResult(
            ok=True,
            status="created",
            external_id=external_id,
            safe_payload={
                "request": {**safe_waybill_payload(waybill_code), **safe_caller_payload(caller_id), "workOrderType": work_order_type},
                "response": response.safe_summary.get("response"),
            },
        )

    def cancel_order(self, *, waybill_code: str, reason_code: str, caller_id: str) -> SpeedafActionResult:
        if not _enabled("SPEEDAF_CANCEL_ENABLED", False):
            raise SpeedafActionDisabled("speedaf_cancel_disabled")
        payload = {"waybillCode": waybill_code, "reasonCode": reason_code, "callerID": caller_id}
        return self._post_action("cancel_order", ORDER_CANCEL_PATH, payload)

    def submit_update_address_flow(self, *, waybill_code: str, whatsapp_phone: str, caller_id: str) -> SpeedafActionResult:
        if not _enabled("SPEEDAF_UPDATE_ADDRESS_ENABLED", False):
            raise SpeedafActionDisabled("speedaf_update_address_disabled")
        payload = {"waybillCode": waybill_code, "whatsAppPhone": whatsapp_phone, "callerID": caller_id}
        return self._post_action("update_address_flow", UPDATE_ADDRESS_PATH, payload)

    def send_voice_callback(self, payload: dict[str, Any]) -> SpeedafActionResult:
        if not _enabled("SPEEDAF_VOICE_CALLBACK_ENABLED", False):
            raise SpeedafActionDisabled("speedaf_voice_callback_disabled")
        return self._post_action("voice_callback", VOICE_CALLBACK_PATH, payload)

    def _post_action(self, action_type: str, path: str, payload: dict[str, Any]) -> SpeedafActionResult:
        try:
            response = self.client.post(path, payload)
        except SpeedafMcpClientError as exc:
            return SpeedafActionResult(
                ok=False,
                action_type=action_type,
                status="failed",
                error_code=exc.error.code,
                error_message=exc.error.message,
                safe_payload=exc.safe_payload,
            )
        return SpeedafActionResult(
            ok=True,
            action_type=action_type,
            status="success",
            safe_payload={"request": redact_mapping(payload), "response": response.safe_summary.get("response")},
        )
