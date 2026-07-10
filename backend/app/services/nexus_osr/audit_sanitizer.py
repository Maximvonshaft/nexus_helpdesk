from __future__ import annotations

from collections.abc import Mapping, MutableSet, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
import hashlib
import json
import math
import re
from typing import Any

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d .()\-]{6,}\d)(?!\w)")
_TRACKING_RE = re.compile(r"\b(?=[A-Z0-9._-]{8,48}\b)(?=(?:[A-Z0-9._-]*\d){4})(?=[A-Z0-9._-]*[A-Z])[A-Z0-9][A-Z0-9._-]+\b", re.I)
_PROVIDER_GROUP_RE = re.compile(r"\b\d{10,24}@g\.us\b", re.I)
_SECRET_RE = re.compile(
    r"(?:\bbearer\s+[A-Za-z0-9._~+/=-]{8,}|\bsk-(?:proj-)?[A-Za-z0-9_-]{12,}|"
    r"\b(?:password|secret|api[_-]?key|credential|authorization|token)\s*[:=]\s*\S+|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----)",
    re.I,
)
_ADDRESS_RE = re.compile(
    r"\b\d{1,6}\s+[A-Z0-9][A-Z0-9 .'-]{2,80}\s(?:street|st\.?|road|rd\.?|avenue|ave\.?|"
    r"boulevard|blvd\.?|lane|ln\.?|drive|dr\.?|ulica|put|strasse|straße)\b",
    re.I,
)
_ISO_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:?\d{2})?)?$")
_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")
_TIMESTAMP_KEYS = {"created_at", "updated_at", "observed_at", "checked_at", "timestamp", "closed_at", "expires_at"}
_TOOL_ARGUMENT_KEYS = {"arguments", "tool_arguments", "tool_args"}
_SAFE_TOOL_ARGUMENT_KEYS = {
    "ticket_id",
    "handoff_request_id",
    "conversation_id",
    "case_context_id",
    "operator_task_id",
}
_TEXT_REDACT_KEYS = {
    "customer_claim_summary",
    "agent_handover_summary",
}
_SAFE_EXACT_KEYS = {
    "authority", "authority_level", "source_type", "evidence_type", "policy_key", "rule_key", "risk_key",
    "status", "safe_status", "failure_category", "error_category", "business_reply_type", "next_action",
    "risk_level", "tool_name", "code", "severity", *_TIMESTAMP_KEYS, "confidence", "allowed", "executed",
    "verified", "current_status", "customer_visible", "handoff_required", "ticket_required", "routing_required",
    "requires_confirmation", "safe_tracking_reference", "tracking_number_hash", "tracking_number_hash_present",
    "sha256_prefix", "destination_group_hash", "destination_group_key", "destination_group_id_hash",
    "destination_group_id_present", "fallback_group_hash", "fallback_group_key", "fallback_group_id_hash",
    "fallback_group_id_present", "present", "redacted", "type", "category", "count", "size", "length", "item_count",
}
_SENSITIVE_KEY_RE = re.compile(
    r"(?:^|_)(?:raw|prompt|system_prompt|developer_prompt|user_prompt|customer_reply|customer_claim|"
    r"message|user_message|assistant_message|message_body|body_text|input_text|output_text|content|transcript|"
    r"provider_payload|provider_request|provider_response|provider_body|tool_result|tool_results|tracking_number|"
    r"phone|email|postal_address|street_address|address|credential|credentials|api_key|authorization|bearer|"
    r"cookie|session_secret|token|password|secret|private_key|provider_group_id|destination_group_id|"
    r"fallback_group_id)(?:$|_)",
    re.I,
)
_SAFE_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$")


@dataclass(frozen=True)
class AuditSanitizerLimits:
    max_depth: int = 6
    max_mapping_items: int = 64
    max_sequence_items: int = 32
    max_string_length: int = 240
    max_key_length: int = 80


DEFAULT_LIMITS = AuditSanitizerLimits()


def sanitize_audit_payload(value: Any, *, limits: AuditSanitizerLimits = DEFAULT_LIMITS) -> Any:
    try:
        return _sanitize(value, key="", depth=0, seen=set(), limits=limits)
    except Exception:
        return {
            "redacted": True,
            "category": "sanitizer_failure",
            "type": _safe_type_name(value),
            "present": _present(value),
            "sha256_prefix": _hash_prefix(value),
        }


def safe_audit_label(value: Any, *, fallback: str, max_length: int = 160) -> str:
    if isinstance(value, Enum):
        value = value.value
    text = " ".join(str(value or "").strip().split())[:max_length]
    if not text or not _SAFE_LABEL_RE.fullmatch(text) or _contains_sensitive_value(text):
        return fallback
    return text


def _sanitize(value: Any, *, key: str, depth: int, seen: MutableSet[int], limits: AuditSanitizerLimits) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else _marker(value, category="non_finite_number")
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return _sanitize(value.value, key=key, depth=depth, seen=seen, limits=limits)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, str):
        return _sanitize_text(value, key=key, limits=limits)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return _marker(value, category="binary")
    if isinstance(value, BaseException):
        return {"redacted": True, "category": "exception", "type": _safe_type_name(value), "present": True}
    if depth >= limits.max_depth:
        return _marker(value, category="max_depth")
    if isinstance(value, Mapping):
        return _sanitize_mapping(value, depth=depth, seen=seen, limits=limits)
    if isinstance(value, (list, tuple)):
        return _sanitize_sequence(value, key=key, depth=depth, seen=seen, limits=limits)
    if isinstance(value, (set, frozenset)):
        sanitized = _sanitize_sequence(list(value), key=key, depth=depth, seen=seen, limits=limits)
        return sorted(sanitized, key=_canonical_json) if isinstance(sanitized, list) else sanitized
    return {"redacted": True, "category": "unsupported_object", "type": _safe_type_name(value), "present": True}


def _sanitize_mapping(value: Mapping[Any, Any], *, depth: int, seen: MutableSet[int], limits: AuditSanitizerLimits) -> dict[str, Any]:
    object_id = id(value)
    if object_id in seen:
        return _marker(value, category="cycle")
    seen.add(object_id)
    try:
        items: list[tuple[str, str, Any]] = []
        for raw_key, raw_value in list(value.items())[:limits.max_mapping_items]:
            source_key = str(raw_key)
            items.append((_safe_key(source_key, limits=limits), source_key, raw_value))
        items.sort(key=lambda item: (item[0], item[1]))
        result: dict[str, Any] = {}
        for safe_key, source_key, raw_value in items:
            output_key = safe_key if safe_key not in result else f"{safe_key}:{_hash_prefix(source_key)}"
            normalized_key = source_key.lower()
            if normalized_key in _TOOL_ARGUMENT_KEYS:
                result[output_key] = _sanitize_tool_arguments(raw_value, limits=limits)
            elif _is_sensitive_key(source_key):
                result[output_key] = _marker(raw_value, category=_sensitive_category(source_key))
            else:
                result[output_key] = _sanitize(raw_value, key=source_key, depth=depth + 1, seen=seen, limits=limits)
        if len(value) > limits.max_mapping_items:
            result["__truncated_keys__"] = len(value) - limits.max_mapping_items
        return result
    finally:
        seen.discard(object_id)


def _sanitize_tool_arguments(value: Any, *, limits: AuditSanitizerLimits) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return _marker(value, category="tool_arguments")

    safe: dict[str, Any] = {}
    redacted_count = 0
    for raw_key, raw_value in sorted(value.items(), key=lambda item: str(item[0]))[:limits.max_mapping_items]:
        key = str(raw_key).lower()
        if key not in _SAFE_TOOL_ARGUMENT_KEYS:
            redacted_count += 1
            continue
        normalized = _safe_identifier(raw_value)
        if normalized is None:
            redacted_count += 1
            continue
        safe[key] = normalized

    if len(value) > limits.max_mapping_items:
        redacted_count += len(value) - limits.max_mapping_items
    if redacted_count:
        safe["redacted"] = True
        safe["redacted_field_count"] = redacted_count
    if safe:
        return safe
    return _marker(value, category="tool_arguments")


def _safe_identifier(value: Any) -> int | str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if _SAFE_IDENTIFIER_RE.fullmatch(text) and not _contains_sensitive_value(text):
            return text
    return None


def _sanitize_sequence(value: Sequence[Any], *, key: str, depth: int, seen: MutableSet[int], limits: AuditSanitizerLimits) -> list[Any] | dict[str, Any]:
    object_id = id(value)
    if object_id in seen:
        return _marker(value, category="cycle")
    seen.add(object_id)
    try:
        result = [_sanitize(item, key=key, depth=depth + 1, seen=seen, limits=limits) for item in list(value)[:limits.max_sequence_items]]
        if len(value) > limits.max_sequence_items:
            result.append({"__truncated_items__": len(value) - limits.max_sequence_items})
        return result
    finally:
        seen.discard(object_id)


def _sanitize_text(value: str, *, key: str, limits: AuditSanitizerLimits) -> str | dict[str, Any]:
    text = " ".join(value.strip().split())
    if not text:
        return ""
    normalized_key = key.lower()
    if normalized_key in _TIMESTAMP_KEYS and _ISO_TIMESTAMP_RE.fullmatch(text):
        return text[:limits.max_string_length]
    if _is_sensitive_key(key):
        return _marker(value, category=_sensitive_category(key))
    text = _SECRET_RE.sub("[redacted_secret]", text)
    text = _PROVIDER_GROUP_RE.sub("[redacted_provider_group]", text)
    text = _EMAIL_RE.sub("[redacted_email]", text)
    text = _PHONE_RE.sub("[redacted_phone]", text)
    if "address" in normalized_key or _ADDRESS_RE.search(text):
        text = _ADDRESS_RE.sub("[redacted_address]", text)
    if normalized_key not in _SAFE_EXACT_KEYS and not normalized_key.endswith(("_hash", "_hash_present", "_present")):
        text = _TRACKING_RE.sub("[redacted_tracking]", text)
    return _truncate_text(text, limit=limits.max_string_length)


def _truncate_text(text: str, *, limit: int) -> str:
    if len(text) <= limit:
        return text
    suffix = f"...[truncated:{_hash_prefix(text)}]"
    if limit <= len(suffix):
        return suffix[:limit]
    return text[:limit - len(suffix)] + suffix


def _safe_key(value: str, *, limits: AuditSanitizerLimits) -> str:
    normalized = " ".join(value.strip().split())
    if not normalized or len(normalized) > limits.max_key_length or _contains_sensitive_value(normalized) or not re.fullmatch(r"[A-Za-z0-9_.:-]+", normalized):
        return f"redacted_key:{_hash_prefix(normalized)}"
    return normalized


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower()
    if normalized in _SAFE_EXACT_KEYS or normalized in _TEXT_REDACT_KEYS or normalized in _TOOL_ARGUMENT_KEYS:
        return False
    if normalized.endswith(("_hash", "_hash_present", "_present", "_count", "_size")):
        return False
    return bool(_SENSITIVE_KEY_RE.search(normalized))


def _sensitive_category(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")[:64] or "sensitive_value"


def _contains_sensitive_value(value: str) -> bool:
    return bool(_SECRET_RE.search(value) or _PROVIDER_GROUP_RE.search(value) or _EMAIL_RE.search(value) or _PHONE_RE.search(value) or _ADDRESS_RE.search(value) or _TRACKING_RE.search(value))


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bool(len(value))
    return True


def _marker(value: Any, *, category: str) -> dict[str, Any]:
    return {"redacted": True, "category": category, "type": _safe_type_name(value), "present": _present(value), "sha256_prefix": _hash_prefix(value)}


def _safe_type_name(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", type(value).__name__)[:64] or "unknown"


def _hash_prefix(value: Any) -> str:
    try:
        if isinstance(value, bytes):
            material = value
        elif isinstance(value, (bytearray, memoryview)):
            material = bytes(value)
        elif isinstance(value, str):
            material = value.encode("utf-8", errors="ignore")
        elif value is None or isinstance(value, (bool, int, float, Decimal)):
            material = str(value).encode("utf-8", errors="ignore")
        else:
            material = json.dumps(value, ensure_ascii=False, sort_keys=True, default=lambda item: {"type": _safe_type_name(item)}).encode("utf-8", errors="ignore")
    except Exception:
        material = _safe_type_name(value).encode("utf-8", errors="ignore")
    return hashlib.sha256(material).hexdigest()[:16]


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
