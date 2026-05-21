from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.services.speedaf.adapter import SpeedafCoreAdapter
from app.services.speedaf.client import SpeedafMcpClientError, load_speedaf_mcp_config
from app.services.tracking_fact_schema import hash_tracking_number

WRITE_FLAGS = (
    "SPEEDAF_WORK_ORDER_CREATE_ENABLED",
    "SPEEDAF_UPDATE_ADDRESS_ENABLED",
    "SPEEDAF_CANCEL_ENABLED",
    "SPEEDAF_VOICE_CALLBACK_ENABLED",
)


def env_bool(name: str) -> bool:
    return str(os.getenv(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def fail(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"ok": False, "code": code, "message": message, "details": details or {}}


def safe_config_summary() -> dict[str, Any]:
    config = load_speedaf_mcp_config()
    return {
        "enabled": config.enabled,
        "configured": config.configured,
        "base_url": config.base_url,
        "app_code_present": bool(config.app_code),
        "secret_key_present": bool(config.secret_key),
        "timeout_seconds": config.timeout_seconds,
        "country_code_default": config.country_code_default,
        "content_type": config.content_type,
        "data_mode": config.data_mode,
        "require_sign": config.require_sign,
    }


def guard_write_flags() -> dict[str, Any] | None:
    enabled = [name for name in WRITE_FLAGS if env_bool(name)]
    if enabled:
        return fail("write_flags_enabled", "Readonly UAT probe refuses to run while Speedaf write flags are enabled.", {"enabled_flags": enabled})
    return None


def run_order_query(adapter: SpeedafCoreAdapter, *, waybill_code: str, caller_id: str | None) -> dict[str, Any]:
    started = time.monotonic()
    try:
        result = adapter.query_order_tracking_fact(waybill_code=waybill_code, caller_id=caller_id)
        return {
            "ok": result.ok,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "tool_status": result.tool_status,
            "fact_evidence_present": result.fact_evidence_present,
            "pii_redacted": result.pii_redacted,
            "status": result.status,
            "status_label": result.status_label,
            "failure_reason": result.failure_reason,
            "tracking_number_hash": hash_tracking_number(waybill_code),
            "metadata": result.metadata_payload(),
        }
    except SpeedafMcpClientError as exc:
        return {
            "ok": False,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "error_code": exc.error.code,
            "error_message": exc.error.message,
            "retryable": exc.error.retryable,
            "safe_payload": exc.safe_payload,
        }
    except Exception as exc:
        return {"ok": False, "elapsed_ms": int((time.monotonic() - started) * 1000), "error_code": type(exc).__name__, "error_message": str(exc)}


def run_waybill_lookup(adapter: SpeedafCoreAdapter, *, caller_id: str, country_code: str | None) -> dict[str, Any]:
    started = time.monotonic()
    try:
        result = adapter.query_waybills_by_caller(caller_id=caller_id, country_code=country_code)
        return {
            "ok": result.ok,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "failure_reason": result.failure_reason,
            "candidate_count": len(result.candidates),
            "safe_candidates": [
                {"waybill_suffix": item.waybill_code_suffix, "waybill_hash": hash_tracking_number(item.waybill_code)}
                for item in result.candidates[:10]
            ],
        }
    except SpeedafMcpClientError as exc:
        return {
            "ok": False,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "error_code": exc.error.code,
            "error_message": exc.error.message,
            "retryable": exc.error.retryable,
            "safe_payload": exc.safe_payload,
        }
    except Exception as exc:
        return {"ok": False, "elapsed_ms": int((time.monotonic() - started) * 1000), "error_code": type(exc).__name__, "error_message": str(exc)}


def write_report(path: str | None, payload: dict[str, Any]) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Readonly Speedaf UAT probe for NexusDesk.")
    parser.add_argument("--waybill-code", default=os.getenv("SPEEDAF_UAT_WAYBILL_CODE"), help="Optional UAT waybill code for order/query.")
    parser.add_argument("--caller-id", default=os.getenv("SPEEDAF_UAT_CALLER_ID"), help="Optional UAT callerID for waybillCode/query and order/query contract.")
    parser.add_argument("--country-code", default=os.getenv("SPEEDAF_UAT_COUNTRY_CODE") or os.getenv("SPEEDAF_MCP_COUNTRY_CODE_DEFAULT") or "CH")
    parser.add_argument("--output-json", default=os.getenv("SPEEDAF_UAT_OUTPUT_JSON"), help="Optional output path for a redacted JSON report.")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero if any requested live read-only check fails.")
    args = parser.parse_args(argv)

    report: dict[str, Any] = {
        "ok": True,
        "probe": "speedaf_readonly_uat_probe",
        "config": safe_config_summary(),
        "guards": {},
        "checks": {},
        "notes": [
            "This probe is read-only.",
            "It refuses to run when any Speedaf write feature flag is enabled.",
            "It never prints appCode, secretKey, raw callerID, or full waybillCode.",
        ],
    }

    guard_failure = guard_write_flags()
    if guard_failure:
        report["ok"] = False
        report["guards"]["write_flags"] = guard_failure
        write_report(args.output_json, report)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    report["guards"]["write_flags"] = {"ok": True, "enabled_flags": []}

    config = load_speedaf_mcp_config()
    if not config.configured:
        report["ok"] = False
        report["checks"]["configuration"] = fail("speedaf_mcp_not_configured", "SPEEDAF_MCP_ENABLED and SPEEDAF_MCP_APP_CODE are required for live UAT.")
        write_report(args.output_json, report)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 2

    if config.require_sign:
        report["checks"]["sign_rule"] = fail("sign_rule_required", "SPEEDAF_MCP_REQUIRE_SIGN=true but the current client intentionally does not guess the sign algorithm.")
        report["ok"] = False
        write_report(args.output_json, report)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 2

    adapter = SpeedafCoreAdapter()
    if args.waybill_code:
        report["checks"]["order_query"] = run_order_query(adapter, waybill_code=args.waybill_code, caller_id=args.caller_id)
    else:
        report["checks"]["order_query"] = {"ok": None, "skipped": True, "reason": "missing_waybill_code"}

    if args.caller_id:
        report["checks"]["waybill_code_query"] = run_waybill_lookup(adapter, caller_id=args.caller_id, country_code=args.country_code)
    else:
        report["checks"]["waybill_code_query"] = {"ok": None, "skipped": True, "reason": "missing_caller_id"}

    requested_results = [value for value in report["checks"].values() if isinstance(value, dict) and value.get("ok") is not None]
    if any(value.get("ok") is False for value in requested_results):
        report["ok"] = False

    write_report(args.output_json, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if args.strict and not report["ok"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
