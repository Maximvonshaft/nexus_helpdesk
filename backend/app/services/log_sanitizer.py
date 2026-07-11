from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .nexus_osr.audit_sanitizer import AuditSanitizerLimits, safe_audit_label, sanitize_audit_payload

LOG_LIMITS = AuditSanitizerLimits(
    max_depth=5,
    max_mapping_items=48,
    max_sequence_items=24,
    max_string_length=320,
    max_key_length=80,
)
_RESERVED_FIELDS = {"level", "logger", "message"}


def sanitize_log_message(value: Any) -> str:
    """Return one bounded redacted log message without raising."""

    try:
        payload = sanitize_audit_payload({"log_text": value}, limits=LOG_LIMITS)
        text = payload.get("log_text") if isinstance(payload, Mapping) else None
        if isinstance(text, str):
            return text or "empty_log_message"
        return "redacted_log_message"
    except Exception:
        return "log_sanitizer_failure"


def sanitize_log_event(value: Any) -> dict[str, Any]:
    """Sanitize arbitrary structured logging fields at the terminal boundary."""

    try:
        if not isinstance(value, Mapping):
            value = {"event_value": value}
        sanitized = sanitize_audit_payload(value, limits=LOG_LIMITS)
        if not isinstance(sanitized, Mapping):
            return {"redacted": True, "category": "invalid_log_event"}
        result: dict[str, Any] = {}
        for key, item in sanitized.items():
            safe_key = str(key)
            if safe_key in _RESERVED_FIELDS:
                safe_key = f"event_{safe_key}"
            result[safe_key] = item
        return result
    except Exception:
        return {"redacted": True, "category": "log_sanitizer_failure"}


def build_safe_log_payload(
    *,
    level: Any,
    logger: Any,
    message: Any,
    event_payload: Any = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "level": safe_audit_label(level, fallback="UNKNOWN", max_length=24),
        "logger": safe_audit_label(logger, fallback="unknown_logger", max_length=120),
        "message": sanitize_log_message(message),
    }
    if event_payload is not None:
        payload.update(sanitize_log_event(event_payload))
    return payload
