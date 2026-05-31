from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from app.services.speedaf.action_service import SpeedafActionDisabled, SpeedafActionService
from app.services.speedaf.adapter import SpeedafCoreAdapter
from app.services.speedaf.client import load_speedaf_mcp_config
from app.services.speedaf.redactor import safe_waybill_payload

WRITE_ACK = "I_UNDERSTAND_SPEEDAF_UAT_WRITES"


def _check(ok: bool, **kwargs: Any) -> dict[str, Any]:
    return {"ok": bool(ok), **kwargs}


def _safe_error(exc: Exception) -> dict[str, Any]:
    return {"error_type": type(exc).__name__, "message": str(exc)[:240]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run full Speedaf UAT probe with explicit write acknowledgement.")
    parser.add_argument("--waybill-code", default=os.getenv("SPEEDAF_MCP_TEST_WAYBILL_CODE") or "")
    parser.add_argument("--caller-id", default=os.getenv("SPEEDAF_MCP_TEST_CALLER_ID") or "")
    parser.add_argument("--whatsapp-phone", default=os.getenv("SPEEDAF_UAT_TEST_WHATSAPP_PHONE") or "")
    parser.add_argument("--cancel-reason", default=os.getenv("SPEEDAF_UAT_CANCEL_REASON_CODE") or "CC01")
    parser.add_argument("--country-code", default=os.getenv("SPEEDAF_MCP_COUNTRY_CODE_DEFAULT") or "CH")
    parser.add_argument("--write-ack", default=os.getenv("SPEEDAF_FULL_UAT_WRITE_ACK") or "")
    parser.add_argument("--output-json", default="")
    args = parser.parse_args()

    config = load_speedaf_mcp_config()
    report: dict[str, Any] = {
        "probe": "speedaf_full_uat_probe",
        "ok": True,
        "config": {
            "configured": config.configured,
            "enabled": config.enabled,
            "base_url": config.base_url,
            "app_code_present": bool(config.app_code),
            "secret_key_present": bool(config.secret_key),
            "content_type": config.content_type,
            "data_mode": config.data_mode,
            "require_sign": config.require_sign,
        },
        "checks": {},
        "write_acknowledged": args.write_ack == WRITE_ACK,
        "notes": [
            "This probe redacts appCode, secretKey, callerID, WhatsApp phone, and full waybillCode.",
            "Write/system actions run only when --write-ack matches the required acknowledgement string.",
        ],
    }
    if not config.configured:
        report["ok"] = False
        report["checks"]["configuration"] = _check(False, failure_reason="speedaf_mcp_not_configured")
    if config.require_sign:
        report["ok"] = False
        report["checks"]["sign_rule"] = _check(False, failure_reason="sign_rule_not_configured")

    adapter = SpeedafCoreAdapter()
    waybill = args.waybill_code.strip().upper()
    caller = args.caller_id.strip()
    if waybill and caller and report["ok"]:
        fact = adapter.query_order_tracking_fact(waybill_code=waybill, caller_id=caller)
        report["checks"]["order_query"] = _check(
            fact.ok and fact.fact_evidence_present and fact.pii_redacted,
            tool_status=fact.tool_status,
            failure_reason=fact.failure_reason,
            status=fact.status,
            status_label=fact.status_label,
            metadata=fact.metadata_payload(),
        )
    else:
        report["checks"]["order_query"] = _check(False, failure_reason="missing_waybill_or_caller")

    if caller and report["ok"]:
        lookup = adapter.query_waybills_by_caller(caller_id=caller, country_code=args.country_code)
        report["checks"]["waybill_code_query"] = _check(
            lookup.ok,
            failure_reason=lookup.failure_reason,
            candidate_count=len(lookup.candidates),
            safe_candidates=[safe_waybill_payload(item.waybill_code) | {"waybill_suffix": item.suffix} for item in lookup.candidates[:10]],
        )
    else:
        report["checks"]["waybill_code_query"] = _check(False, failure_reason="missing_caller")

    if args.write_ack != WRITE_ACK:
        report["checks"]["work_order_create"] = _check(False, skipped=True, failure_reason="write_ack_required")
        report["checks"]["address_update"] = _check(False, skipped=True, failure_reason="write_ack_required")
        report["checks"]["cancel_order"] = _check(False, skipped=True, failure_reason="write_ack_required")
        report["checks"]["voice_callback"] = _check(False, skipped=True, failure_reason="write_ack_required")
    elif report["ok"]:
        service = SpeedafActionService()
        try:
            result = service.create_work_order(waybill_code=waybill, caller_id=caller, work_order_type="WT0103-05", description="Nexus full UAT probe delivery follow-up")
            report["checks"]["work_order_create"] = _check(result.ok, status=result.status, error_code=result.error_code, safe_payload=result.safe_payload)
        except (SpeedafActionDisabled, Exception) as exc:
            report["checks"]["work_order_create"] = _check(False, **_safe_error(exc))
        try:
            result = service.submit_update_address_flow(waybill_code=waybill, caller_id=caller, whatsapp_phone=args.whatsapp_phone.strip())
            report["checks"]["address_update"] = _check(result.ok, status=result.status, error_code=result.error_code, safe_payload=result.safe_payload)
        except (SpeedafActionDisabled, Exception) as exc:
            report["checks"]["address_update"] = _check(False, **_safe_error(exc))
        try:
            result = service.cancel_order(waybill_code=waybill, caller_id=caller, reason_code=args.cancel_reason.strip().upper())
            report["checks"]["cancel_order"] = _check(result.ok, status=result.status, error_code=result.error_code, safe_payload=result.safe_payload)
        except (SpeedafActionDisabled, Exception) as exc:
            report["checks"]["cancel_order"] = _check(False, **_safe_error(exc))
        try:
            result = service.send_voice_callback({
                "callSessionId": "nexus-uat-probe",
                "isTransferredToHuman": 1,
                "action": {
                    "waybillCode": waybill,
                    "action": "查询订单",
                    "actionTime": "2026-06-01 00:00:00",
                    "aiActionSummary": "Nexus full UAT probe voice callback",
                    "actionStatus": "SUCCESS",
                    "errorCode": "",
                },
            })
            report["checks"]["voice_callback"] = _check(result.ok, status=result.status, error_code=result.error_code, safe_payload=result.safe_payload)
        except (SpeedafActionDisabled, Exception) as exc:
            report["checks"]["voice_callback"] = _check(False, **_safe_error(exc))

    report["ok"] = bool(report["ok"] and report["checks"].get("order_query", {}).get("ok"))
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output_json:
        Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_json).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
