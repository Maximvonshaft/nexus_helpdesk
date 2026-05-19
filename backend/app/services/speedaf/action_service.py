from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

from ..tool_governance import record_tool_call
from .client import SpeedafMcpClient, SpeedafMcpClientError
from .redactor import redact_mapping, safe_caller_payload, safe_waybill_payload
from .schemas import SpeedafWorkOrderResult
from .status_map import is_auto_work_order_type_allowed

WORK_ORDER_CREATE_PATH = "/open-api/mcp/workOrder/create"
ORDER_CANCEL_PATH = "/open-api/mcp/order/cancel"
UPDATE_ADDRESS_PATH = "/open-api/mcp/order/updateAddress"
VOICE_CALLBACK_PATH = "/open-api/mcp/callData/voice/callBack"

ACTION_TOOL_NAMES = {
    "work_order_create": "speedaf.work_order.create",
    "cancel_order": "speedaf.order.cancel",
    "update_address_flow": "speedaf.order.update_address",
    "voice_callback": "speedaf.voice.callback",
}


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

    def __init__(
        self,
        client: SpeedafMcpClient | None = None,
        *,
        ticket_id: int | None = None,
        webchat_conversation_id: int | None = None,
        background_job_id: int | None = None,
        request_id: str | None = None,
    ) -> None:
        self.client = client or SpeedafMcpClient()
        self.ticket_id = ticket_id
        self.webchat_conversation_id = webchat_conversation_id
        self.background_job_id = background_job_id
        self.request_id = request_id

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
        started = time.monotonic()
        try:
            response = self.client.post(WORK_ORDER_CREATE_PATH, payload)
        except SpeedafMcpClientError as exc:
            safe_payload = exc.safe_payload
            self._record_action_audit(
                action_type="work_order_create",
                payload=payload,
                output_payload=safe_payload,
                status="failed",
                error_code=exc.error.code,
                error_message=exc.error.message,
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
            return SpeedafWorkOrderResult(
                ok=False,
                status="failed",
                error_code=exc.error.code,
                error_message=exc.error.message,
                safe_payload=safe_payload,
            )
        data = response.data if isinstance(response.data, dict) else {}
        external_id = str(data.get("workOrderCode") or data.get("workOrderId") or data.get("id") or "").strip() or None
        safe_payload = {
            "request": {**safe_waybill_payload(waybill_code), **safe_caller_payload(caller_id), "workOrderType": work_order_type},
            "response": response.safe_summary.get("response"),
        }
        self._record_action_audit(
            action_type="work_order_create",
            payload=payload,
            output_payload=safe_payload,
            status="success",
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
        return SpeedafWorkOrderResult(
            ok=True,
            status="created",
            external_id=external_id,
            safe_payload=safe_payload,
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
        started = time.monotonic()
        try:
            response = self.client.post(path, payload)
        except SpeedafMcpClientError as exc:
            self._record_action_audit(
                action_type=action_type,
                payload=payload,
                output_payload=exc.safe_payload,
                status="failed",
                error_code=exc.error.code,
                error_message=exc.error.message,
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
            return SpeedafActionResult(
                ok=False,
                action_type=action_type,
                status="failed",
                error_code=exc.error.code,
                error_message=exc.error.message,
                safe_payload=exc.safe_payload,
            )
        safe_payload = {"request": redact_mapping(payload), "response": response.safe_summary.get("response")}
        self._record_action_audit(
            action_type=action_type,
            payload=payload,
            output_payload=safe_payload,
            status="success",
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
        return SpeedafActionResult(
            ok=True,
            action_type=action_type,
            status="success",
            safe_payload=safe_payload,
        )

    def _record_action_audit(
        self,
        *,
        action_type: str,
        payload: dict[str, Any],
        output_payload: dict[str, Any] | None,
        status: str,
        error_code: str | None = None,
        error_message: str | None = None,
        elapsed_ms: int | None = None,
    ) -> None:
        tool_name = ACTION_TOOL_NAMES.get(action_type, f"speedaf.{action_type}")
        record_tool_call(
            tool_name=tool_name,
            provider="speedaf_mcp",
            tool_type="system" if action_type == "voice_callback" else "write_action",
            input_payload=redact_mapping(payload),
            output_payload=output_payload,
            status=status,
            error_code=error_code,
            error_message=error_message,
            elapsed_ms=elapsed_ms,
            webchat_conversation_id=self.webchat_conversation_id,
            ticket_id=self.ticket_id,
            background_job_id=self.background_job_id,
            request_id=self.request_id,
        )
