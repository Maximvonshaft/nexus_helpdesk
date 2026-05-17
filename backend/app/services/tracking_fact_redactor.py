from __future__ import annotations

import re
from typing import Any

from .tracking_fact_schema import TrackingFactEvent, TrackingFactResult

_EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{6,}\d)(?!\w)")
_ID_LIKE_RE = re.compile(r"\b(?:passport|id card|national id|身份证|护照)\b\s*[:：]?\s*[A-Z0-9-]{4,}", re.IGNORECASE)
_ADDRESS_HINT_RE = re.compile(r"\b(?:street|strasse|straße|road|avenue|apt|apartment|unit|floor|room|door|building|house|地址|门牌|房间|楼层)\b", re.IGNORECASE)
_POD_NAME_EN_RE = re.compile(
    r"\b(?P<prefix>signed by|received by|receiver|recipient|consignee|delivered to)\s*[:：]?\s+(?P<name>[A-Z][A-Za-z .'-]{1,80})",
    re.IGNORECASE,
)
_POD_NAME_ZH_RE = re.compile(r"(?P<prefix>签收人|收件人|收货人)\s*[:：]?\s*(?P<name>[\u4e00-\u9fffA-Za-z .'-]{1,30})")
_NO_TRACE_RE = re.compile(r"\b(no\s*info|no\s*tracking|no\s*trace|no\s*updates?)\b|暂无轨迹|暂无物流|暂无更新", re.IGNORECASE)

PII_KEYS = {
    "recipient",
    "recipient_name",
    "receiver",
    "receiver_name",
    "consignee",
    "consignee_name",
    "signer",
    "signed_by",
    "pod_name",
    "pod_signer",
    "name",
    "phone",
    "mobile",
    "telephone",
    "email",
    "address",
    "full_address",
    "street",
    "house_no",
    "id_number",
    "passport",
}


def mask_name(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text:
        return None
    compact = re.sub(r"\s+", " ", text)
    if len(compact) <= 1:
        return "*"
    if len(compact) == 2:
        return compact[0] + "*"
    return compact[0] + "***" + compact[-1]


def _redact_embedded_pod_names(text: str) -> str:
    text = _POD_NAME_EN_RE.sub(lambda m: f"{m.group('prefix')} [redacted_name]", text)
    text = _POD_NAME_ZH_RE.sub(lambda m: f"{m.group('prefix')}[redacted_name]", text)
    return text


def redact_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = _redact_embedded_pod_names(text)
    text = _EMAIL_RE.sub("[redacted_email]", text)
    text = _PHONE_RE.sub("[redacted_phone]", text)
    text = _ID_LIKE_RE.sub("[redacted_id]", text)
    if _ADDRESS_HINT_RE.search(text) and len(text) > 48:
        return "[redacted_address]"
    return text[:240]


def sanitize_payload(value: Any) -> Any:
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = str(key).strip().lower()
            if normalized_key in PII_KEYS:
                if "name" in normalized_key or normalized_key in {"recipient", "receiver", "consignee", "signer", "signed_by"}:
                    clean[f"{key}_redacted"] = mask_name(str(item))
                else:
                    clean[f"{key}_redacted"] = True
                continue
            clean[str(key)] = sanitize_payload(item)
        return clean
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value[:20]]
    if isinstance(value, str):
        return redact_text(value)
    return value


def _first_string(payload: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return redact_text(value)
    return None


def _event_from_payload(payload: Any) -> TrackingFactEvent | None:
    if not isinstance(payload, dict):
        return None
    return TrackingFactEvent(
        event_time=_first_string(payload, ("event_time", "time", "timestamp", "date", "created_at")),
        location=_first_string(payload, ("location", "station", "hub", "facility", "city")),
        description=_first_string(payload, ("description", "desc", "status", "status_label", "event", "message")),
    )


def _looks_like_no_trace_fact(*values: str | None) -> bool:
    joined = "\n".join(value for value in values if value)
    return bool(joined and _NO_TRACE_RE.search(joined))


def normalize_tracking_fact(raw: dict[str, Any], *, tracking_number: str | None) -> TrackingFactResult:
    safe_raw = sanitize_payload(raw)
    if not isinstance(safe_raw, dict):
        return TrackingFactResult(ok=False, tracking_number=tracking_number, tool_status="invalid", pii_redacted=True, failure_reason="invalid_tool_response")

    ok = bool(safe_raw.get("ok", True))
    status = _first_string(safe_raw, ("status", "shipment_status", "parcel_status"))
    status_label = _first_string(safe_raw, ("status_label", "statusLabel", "label")) or status
    message = _first_string(safe_raw, ("message", "description", "desc"))
    no_trace_fact = _looks_like_no_trace_fact(status, status_label, message)
    tool_status = _first_string(safe_raw, ("tool_status", "status_code", "result_status")) or ("success" if ok else ("no_info" if no_trace_fact else "error"))
    checked_at = _first_string(safe_raw, ("checked_at", "checkedAt", "query_time", "time"))
    resolved_tracking_number = _first_string(safe_raw, ("tracking_number", "trackingNumber", "waybill", "waybill_no")) or tracking_number

    latest_event = _event_from_payload(safe_raw.get("latest_event") or safe_raw.get("latestEvent") or safe_raw.get("last_event"))
    events_source = safe_raw.get("events_summary") or safe_raw.get("events") or safe_raw.get("tracking_events") or []
    events_summary: list[TrackingFactEvent] = []
    if isinstance(events_source, list):
        for item in events_source[:5]:
            event = _event_from_payload(item)
            if event and event.is_present():
                events_summary.append(event)
    if latest_event is None and events_summary:
        latest_event = events_summary[0]

    evidence_present = bool((ok or no_trace_fact) and (status or status_label or message or (latest_event and latest_event.is_present()) or events_summary))
    if no_trace_fact and not status_label:
        status_label = "No Info"
    if no_trace_fact and latest_event is None and message:
        latest_event = TrackingFactEvent(description=message)
    return TrackingFactResult(
        ok=ok,
        tracking_number=resolved_tracking_number,
        status=status,
        status_label=status_label,
        latest_event=latest_event,
        events_summary=events_summary,
        checked_at=checked_at,
        source="openclaw_bridge.speedaf_lookup",
        tool_name="speedaf_lookup",
        tool_status=tool_status,
        pii_redacted=True,
        fact_evidence_present=evidence_present,
        failure_reason=None if evidence_present else (None if ok else "tool_lookup_failed"),
    )