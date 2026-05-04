from __future__ import annotations

import re
from typing import Any

from .tracking_fact_schema import TrackingFactEvent, TrackingFactResult, hash_tracking_number, mask_tracking_number

_EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{6,}\d)(?!\w)")
_ID_LIKE_RE = re.compile(r"\b(?:passport|id card|national id|身份证|护照)\b\s*[:：]?\s*[A-Z0-9-]{4,}", re.IGNORECASE)
_ADDRESS_HINT_RE = re.compile(r"\b(?:street|strasse|straße|road|avenue|apt|apartment|unit|floor|room|door|building|house|地址|门牌|房间|楼层)\b", re.IGNORECASE)
_POD_NAME_EN_RE = re.compile(
    r"\b(?P<prefix>signed by|received by|receiver|recipient|consignee|delivered to|courier|driver)\s*[:：]?\s+(?P<name>[A-Z][A-Za-z .'-]{1,80})",
    re.IGNORECASE,
)
_POD_NAME_ZH_RE = re.compile(r"(?P<prefix>签收人|收件人|收货人|派件员|快递员)\s*[:：]?\s*(?P<name>[\u4e00-\u9fffA-Za-z .'-]{1,30})")

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
    "courier",
    "courier_name",
    "delivery_staff",
    "delivery_staff_name",
    "driver",
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

FORBIDDEN_KEYS = {
    "pictureurl",
    "picture_url",
    "proof_tag",
    "internal_summary",
    "raw",
    "raw_payload",
    "raw_upstream_payload",
    "response_wrapper",
    "decrypted_response",
    "decrypted_raw",
    "decrypted_text",
}

_LOCATION_KEYWORDS = {
    "center": "destination hub",
    "centre": "destination hub",
    "station": "local delivery network",
    "warehouse": "warehouse network",
    "hub": "hub network",
    "airport": "airport hub",
    "customs": "customs",
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


def generalize_location_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    lowered = text.lower()
    safe_phrases = {
        "destination hub",
        "hub network",
        "warehouse network",
        "airport hub",
        "customs",
        "local delivery network",
        "origin pickup network",
        "origin warehouse network",
        "origin consolidation network",
        "origin outbound network",
        "origin transfer network",
        "international transit",
        "pickup area",
        "delivery area",
        "logistics network",
    }
    if lowered in safe_phrases or lowered.endswith(" delivery area") or lowered.endswith(" customs"):
        return text
    for needle, replacement in _LOCATION_KEYWORDS.items():
        if needle in lowered:
            return replacement
    return "logistics network"


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
    if _ADDRESS_HINT_RE.search(text) and len(text) > 32:
        return "[redacted_address]"
    return text[:240]


def sanitize_payload(value: Any) -> Any:
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = str(key).strip().lower()
            if normalized_key in FORBIDDEN_KEYS:
                continue
            if normalized_key in PII_KEYS:
                if "name" in normalized_key or normalized_key in {"recipient", "receiver", "consignee", "signer", "signed_by", "courier", "driver"}:
                    clean[f"{key}_redacted"] = mask_name(str(item))
                else:
                    clean[f"{key}_redacted"] = True
                continue
            if normalized_key in {"location", "station", "hub", "facility", "warehouse", "site"}:
                clean[str(key)] = generalize_location_text(item)
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
        location=generalize_location_text(payload.get("location") or payload.get("station") or payload.get("hub") or payload.get("facility") or payload.get("city")),
        description=_first_string(payload, ("description", "desc", "event", "message", "message_safe")),
        milestone=_first_string(payload, ("milestone",)),
        status=_first_string(payload, ("status", "status_label", "label")),
    )


def normalize_tracking_fact(raw: dict[str, Any], *, tracking_number: str | None) -> TrackingFactResult:
    safe_raw = sanitize_payload(raw)
    if not isinstance(safe_raw, dict):
        return TrackingFactResult(ok=False, tracking_number=tracking_number, tool_status="invalid", pii_redacted=True, raw_included=False, failure_reason="invalid_tool_response")

    ok = bool(safe_raw.get("ok", True))
    checked_at = _first_string(safe_raw, ("checked_at", "checkedAt", "query_time", "time"))
    masked = _first_string(safe_raw, ("tracking_number_masked",)) or mask_tracking_number(tracking_number)
    tracking_hash = _first_string(safe_raw, ("tracking_hash",)) or hash_tracking_number(tracking_number)
    status = _first_string(safe_raw, ("latest_status", "status", "shipment_status", "parcel_status"))
    latest_milestone = _first_string(safe_raw, ("latest_milestone", "milestone"))
    summary_safe = _first_string(safe_raw, ("summary_safe",))
    message_safe = _first_string(safe_raw, ("message_safe",))
    tool_status = _first_string(safe_raw, ("tool_status", "status_code", "result_status")) or ("success" if ok else "error")
    source = _first_string(safe_raw, ("source",)) or "speedaf_readonly_adapter"
    risk_level = _first_string(safe_raw, ("risk_level",))
    raw_included = bool(safe_raw.get("raw_included", False))
    pii_redacted = bool(safe_raw.get("pii_redacted", True))
    escalate = bool(safe_raw.get("escalate", False))

    latest_event = TrackingFactEvent(
        event_time=_first_string(safe_raw, ("latest_event_time",)),
        location=generalize_location_text(safe_raw.get("latest_event_location_safe")),
        description=summary_safe or message_safe,
        milestone=latest_milestone,
        status=status,
    )
    if not latest_event.is_present():
        latest_event = _event_from_payload(safe_raw.get("latest_event") or safe_raw.get("latestEvent") or safe_raw.get("last_event"))

    events_source = safe_raw.get("timeline_limited") or safe_raw.get("events_summary") or safe_raw.get("events") or safe_raw.get("tracking_events") or []
    events_summary: list[TrackingFactEvent] = []
    if isinstance(events_source, list):
        for item in events_source[:5]:
            event = _event_from_payload(item)
            if event and event.is_present():
                events_summary.append(event)
    if latest_event is None and events_summary:
        latest_event = events_summary[-1]

    failure_reason = _first_string(safe_raw, ("error", "failure_reason"))
    evidence_present = bool(ok and pii_redacted and not raw_included and (status or summary_safe or (latest_event and latest_event.is_present()) or events_summary))
    return TrackingFactResult(
        ok=ok,
        tracking_number=tracking_number,
        tracking_number_masked=masked,
        tracking_hash=tracking_hash,
        status=status,
        status_label=status,
        latest_milestone=latest_milestone,
        latest_event=latest_event,
        events_summary=events_summary,
        checked_at=checked_at,
        source=source,
        tool_name="speedaf_tracking_readonly_adapter",
        tool_status=tool_status,
        pii_redacted=pii_redacted,
        raw_included=raw_included,
        summary_safe=summary_safe,
        message_safe=message_safe,
        risk_level=risk_level,
        escalate=escalate,
        fact_evidence_present=evidence_present,
        failure_reason=None if evidence_present else failure_reason or (None if ok else "tool_lookup_failed"),
    )
