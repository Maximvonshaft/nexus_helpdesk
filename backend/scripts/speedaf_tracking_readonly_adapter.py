#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Callable

TRACKING_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{7,34}[A-Z0-9]$", re.IGNORECASE)
SOURCE = "speedaf_readonly_adapter"
DEFAULT_SUPPORT_SCRIPTS_DIR = "/home/vboxuser/.openclaw/agents/support/scripts"

_MILESTONE_LABELS = {
    "pickup": "origin_pickup",
    "inbound": "origin_inbound",
    "assembling": "origin_assembly",
    "outbound": "origin_outbound",
    "domestic_handover": "origin_handover",
    "flight_departure": "flight_departure",
    "flight_arrival": "flight_arrival",
    "customs_started": "customs_processing",
    "customs_inspection": "customs_inspection",
    "customs_completed": "customs_completed",
    "customs_exception": "customs_exception",
    "dc_arrival": "destination_hub",
    "delivery_out": "out_for_delivery",
    "delivered": "delivered",
    "pickup_point": "pickup_point",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _mask_tracking_number(tracking_number: str) -> str:
    suffix = tracking_number[-4:] if tracking_number else ""
    return f"****{suffix}" if suffix else "****"


def _hash_tracking_number(tracking_number: str) -> str:
    return "sha256:" + hashlib.sha256(tracking_number.strip().upper().encode("utf-8")).hexdigest()


def _is_valid_tracking_number(value: str) -> bool:
    candidate = value.strip().upper()
    return bool(candidate and TRACKING_RE.match(candidate) and re.search(r"\d", candidate))


def _ensure_support_scripts_importable() -> None:
    scripts_dir = os.getenv("OPENCLAW_SUPPORT_SCRIPTS_DIR", DEFAULT_SUPPORT_SCRIPTS_DIR)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)


def _load_support_functions() -> tuple[Callable[..., dict[str, Any]], Callable[[dict[str, Any]], dict[str, Any]], dict[str, Any], Callable[[dict[str, Any]], bool]]:
    _ensure_support_scripts_importable()
    from speedaf_client import track_query  # type: ignore
    from milestone_engine import analyze_tracking_payload  # type: ignore
    from status_mapper import STATUS_MAP, should_escalate  # type: ignore

    return track_query, analyze_tracking_payload, STATUS_MAP, should_escalate


def _country_prefix(raw: dict[str, Any]) -> str:
    code = str(raw.get("receiverCountryCode") or "").strip().upper()
    return f"{code} " if re.fullmatch(r"[A-Z]{2}", code) else ""


def _safe_location(milestone: str | None, raw: dict[str, Any]) -> str | None:
    prefix = _country_prefix(raw)
    mapping = {
        "pickup": "origin pickup network",
        "inbound": "origin warehouse network",
        "assembling": "origin consolidation network",
        "outbound": "origin outbound network",
        "domestic_handover": "origin transfer network",
        "flight_departure": "international transit",
        "flight_arrival": f"{prefix}arrival hub".strip(),
        "customs_started": f"{prefix}customs".strip(),
        "customs_inspection": f"{prefix}customs".strip(),
        "customs_completed": f"{prefix}customs".strip(),
        "customs_exception": f"{prefix}customs".strip(),
        "dc_arrival": f"{prefix}destination hub".strip(),
        "delivery_out": f"{prefix}local delivery network".strip(),
        "delivered": f"{prefix}delivery area".strip(),
        "pickup_point": f"{prefix}pickup area".strip(),
    }
    value = mapping.get(milestone or "")
    return value or None


def _safe_status(latest: dict[str, Any], status_map: dict[str, Any]) -> str:
    action = str(latest.get("action") or "")
    status_meta = status_map.get(action) or {}
    label = str(status_meta.get("label") or latest.get("actionName") or "").strip()
    return label or "处理中"


def _safe_message_for_event(event: dict[str, Any], status_map: dict[str, Any]) -> str:
    milestone = event.get("milestone")
    status = _safe_status(event, status_map)
    descriptions = {
        "pickup": "Shipment has entered the origin network.",
        "inbound": "Shipment is being processed at origin.",
        "assembling": "Shipment is being consolidated for onward transport.",
        "outbound": "Shipment has left the origin facility.",
        "domestic_handover": "Shipment completed domestic handover.",
        "flight_departure": "Shipment is in international transit.",
        "flight_arrival": "Shipment arrived in the destination country or region.",
        "customs_started": "Shipment is in customs processing.",
        "customs_inspection": "Shipment is under customs inspection.",
        "customs_completed": "Shipment cleared customs.",
        "customs_exception": "Shipment requires customs follow-up.",
        "dc_arrival": "Shipment reached the destination hub.",
        "delivery_out": "Shipment is out for delivery.",
        "delivered": "Shipment is marked as delivered.",
        "pickup_point": "Shipment is ready for pickup.",
    }
    return descriptions.get(milestone, f"Shipment status updated: {status}.")


def _risk_level(*, milestone: str | None, escalate: bool) -> str:
    if escalate:
        return "high"
    if milestone in {"customs_started", "customs_inspection", "customs_completed", "delivery_out"}:
        return "medium"
    if milestone:
        return "low"
    return "unknown"


def _summary_safe(*, latest_status: str, latest_milestone: str | None, latest_event_time: str | None) -> str:
    milestone_label = _MILESTONE_LABELS.get(latest_milestone or "", latest_milestone or "unknown")
    if latest_event_time:
        return f"Latest safe tracking fact: {latest_status} at {latest_event_time} ({milestone_label})."
    return f"Latest safe tracking fact: {latest_status} ({milestone_label})."


def _build_success_payload(*, tracking_number: str, analysis: dict[str, Any], status_map: dict[str, Any], should_escalate_fn: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    timeline = analysis.get("timeline") or []
    if not timeline:
        return _build_error_payload(tracking_number=tracking_number, error="no_tracking_info", message_safe="No safe tracking fact is available yet.")

    latest = timeline[-1]
    raw_latest = latest.get("raw") if isinstance(latest.get("raw"), dict) else {}
    latest_status = _safe_status(latest, status_map)
    latest_milestone = _MILESTONE_LABELS.get(latest.get("milestone") or "", latest.get("milestone") or "unknown")
    latest_event_time = str(latest.get("time_utc") or "").strip() or None
    escalate = bool((analysis.get("risk") or {}).get("escalate_required")) or should_escalate_fn(raw_latest)

    limited = []
    for item in timeline[-3:]:
        limited.append({
            "time": str(item.get("time_utc") or "").strip() or None,
            "milestone": _MILESTONE_LABELS.get(item.get("milestone") or "", item.get("milestone") or "unknown"),
            "status": _safe_status(item, status_map),
            "message_safe": _safe_message_for_event(item, status_map),
        })

    return {
        "ok": True,
        "source": SOURCE,
        "tracking_number_masked": _mask_tracking_number(tracking_number),
        "tracking_hash": _hash_tracking_number(tracking_number),
        "checked_at": _utc_now_iso(),
        "latest_status": latest_status,
        "latest_milestone": latest_milestone,
        "latest_event_time": latest_event_time,
        "latest_event_location_safe": _safe_location(latest.get("milestone"), raw_latest),
        "summary_safe": _summary_safe(latest_status=latest_status, latest_milestone=latest.get("milestone"), latest_event_time=latest_event_time),
        "escalate": escalate,
        "risk_level": _risk_level(milestone=latest.get("milestone"), escalate=escalate),
        "timeline_limited": limited,
        "raw_included": False,
        "pii_redacted": True,
    }


def _build_error_payload(*, tracking_number: str, error: str, message_safe: str) -> dict[str, Any]:
    return {
        "ok": False,
        "source": SOURCE,
        "tracking_number_masked": _mask_tracking_number(tracking_number),
        "tracking_hash": _hash_tracking_number(tracking_number),
        "checked_at": _utc_now_iso(),
        "error": error,
        "message_safe": message_safe,
        "raw_included": False,
        "pii_redacted": True,
    }


def _map_upstream_error(api_resp: dict[str, Any]) -> tuple[str, str]:
    error = str(api_resp.get("error") or "upstream_error")
    error_code = str(api_resp.get("error_code") or "")
    error_message = str(api_resp.get("error_message") or "")
    layer = str(api_resp.get("layer") or "")
    combined = " ".join([error, error_code, error_message, layer]).lower()
    if layer == "timeout" or error_code == "60001" or "timed out" in combined or "timeout" in combined:
        return "upstream_timeout", "Tracking lookup timed out. Please retry later."
    return "upstream_error", "Tracking lookup is temporarily unavailable. Please retry later."


def lookup_tracking_readonly_adapter(
    payload: dict[str, Any],
    *,
    track_query_fn: Callable[..., dict[str, Any]] | None = None,
    analyze_payload_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    status_map: dict[str, Any] | None = None,
    should_escalate_fn: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    tracking_number = str(payload.get("tracking_number") or "").strip().upper()
    if not tracking_number:
        return _build_error_payload(tracking_number="", error="missing_tracking_number", message_safe="Tracking number is required.")
    if not _is_valid_tracking_number(tracking_number):
        return _build_error_payload(tracking_number=tracking_number, error="invalid_tracking_number", message_safe="Tracking number format is invalid.")

    if track_query_fn is None or analyze_payload_fn is None or status_map is None or should_escalate_fn is None:
        track_query_fn, analyze_payload_fn, status_map, should_escalate_fn = _load_support_functions()

    try:
        sink = StringIO()
        with redirect_stdout(sink):
            api_resp = track_query_fn(tracking_number, dry_run=False, debug=False)
    except Exception:
        return _build_error_payload(tracking_number=tracking_number, error="adapter_error", message_safe="Tracking adapter failed before a safe fact could be produced.")

    if not isinstance(api_resp, dict):
        return _build_error_payload(tracking_number=tracking_number, error="adapter_error", message_safe="Tracking adapter returned an invalid response.")

    if not api_resp.get("ok"):
        error_code, message_safe = _map_upstream_error(api_resp)
        return _build_error_payload(tracking_number=tracking_number, error=error_code, message_safe=message_safe)

    try:
        analysis = analyze_payload_fn(api_resp.get("data") or {})
    except Exception:
        return _build_error_payload(tracking_number=tracking_number, error="adapter_error", message_safe="Tracking adapter could not normalize the upstream response.")

    return _build_success_payload(
        tracking_number=tracking_number,
        analysis=analysis,
        status_map=status_map,
        should_escalate_fn=should_escalate_fn,
    )


def _read_payload_from_stdin() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    loaded = json.loads(raw)
    return loaded if isinstance(loaded, dict) else {}


def main() -> int:
    try:
        payload = _read_payload_from_stdin()
    except Exception:
        sys.stdout.write(json.dumps(_build_error_payload(tracking_number="", error="adapter_error", message_safe="Adapter input was invalid."), ensure_ascii=False))
        return 0

    response = lookup_tracking_readonly_adapter(payload)
    sys.stdout.write(json.dumps(response, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
