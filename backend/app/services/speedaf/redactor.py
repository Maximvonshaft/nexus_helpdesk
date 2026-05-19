from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping

SENSITIVE_KEYS = {
    "acceptMobile",
    "accept_mobile",
    "mobile",
    "phone",
    "callerID",
    "callerId",
    "caller_id",
    "acceptAddress",
    "accept_address",
    "address",
    "secretKey",
    "secret_key",
    "appCode",
    "app_code",
    "sign",
    "signature",
    "token",
    "authorization",
}

PHONE_LIKE_RE = re.compile(r"(?<!\d)(\+?\d[\d\s().-]{5,}\d)(?!\d)")


def sha256_prefix(value: Any, *, length: int = 16) -> str | None:
    cleaned = str(value or "").strip()
    if not cleaned:
        return None
    return hashlib.sha256(cleaned.encode("utf-8", errors="ignore")).hexdigest()[:length]


def hash_value(value: Any) -> str | None:
    prefix = sha256_prefix(value, length=64)
    return f"sha256:{prefix}" if prefix else None


def suffix(value: Any, size: int = 4) -> str | None:
    cleaned = re.sub(r"\s+", "", str(value or "").strip())
    return cleaned[-size:] if cleaned else None


def mask_phone(value: Any) -> str | None:
    cleaned = re.sub(r"\D+", "", str(value or ""))
    if not cleaned:
        return None
    if len(cleaned) <= 4:
        return "*" * len(cleaned)
    return f"***{cleaned[-4:]}"


def redact_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value)
    return PHONE_LIKE_RE.sub(lambda m: f"***{re.sub(r'\\D+', '', m.group(1))[-4:]}", text)


def redact_mapping(payload: Mapping[str, Any] | None, *, max_depth: int = 4) -> dict[str, Any]:
    def walk(value: Any, key: str | None = None, depth: int = 0) -> Any:
        if depth > max_depth:
            return {"redacted": True, "type": type(value).__name__}
        if key in SENSITIVE_KEYS:
            if key and "address" in key.lower():
                return {"redacted": True, "type": "address", "sha256_prefix": sha256_prefix(value)}
            if key and ("phone" in key.lower() or "mobile" in key.lower() or "caller" in key.lower()):
                return {"redacted": True, "type": "phone", "suffix": suffix(value), "sha256_prefix": sha256_prefix(value)}
            return {"redacted": True, "type": "secret_or_sensitive", "sha256_prefix": sha256_prefix(value)}
        if isinstance(value, Mapping):
            return {str(k): walk(v, str(k), depth + 1) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(item, key, depth + 1) for item in value[:20]]
        if isinstance(value, str):
            return redact_text(value)
        return value

    return walk(dict(payload or {}))


def safe_caller_payload(caller_id: str | None) -> dict[str, Any]:
    return {
        "caller_id_hash": hash_value(caller_id),
        "caller_id_suffix": suffix(caller_id),
    }


def safe_waybill_payload(waybill_code: str | None) -> dict[str, Any]:
    return {
        "waybill_hash": hash_value((waybill_code or "").upper()),
        "waybill_suffix": suffix(waybill_code),
    }
