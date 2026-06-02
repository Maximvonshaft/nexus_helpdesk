from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

LOGGER = logging.getLogger("nexusdesk")

_SECRETISH_RE = re.compile(r"(?i)(secret|token|password|authorization|bearer|api[_-]?key)")
_TRACKING_RE = re.compile(r"(?<![A-Z0-9])([A-Z0-9][A-Z0-9._-]{7,47})(?![A-Z0-9])", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{7,}\d)(?!\d)")


def _sha(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()


def _safe_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = str(value)
    if _SECRETISH_RE.search(text):
        return "[REDACTED_SECRET]"
    text = _TRACKING_RE.sub(lambda match: f"[TRACKING:{_sha(match.group(1).upper())[:19]}]", text)
    text = _PHONE_RE.sub(lambda match: f"[CALLER:{_sha(match.group(0))[:19]}]", text)
    return text[:1000]


def safe_audit_payload(payload: Any, *, max_depth: int = 4) -> Any:
    if max_depth <= 0:
        return "[TRUNCATED]"
    if isinstance(payload, dict):
        safe: dict[str, Any] = {}
        for key, value in list(payload.items())[:80]:
            key_text = str(key)[:120]
            if _SECRETISH_RE.search(key_text):
                safe[key_text] = "[REDACTED_SECRET]"
                continue
            safe[key_text] = safe_audit_payload(value, max_depth=max_depth - 1)
        return safe
    if isinstance(payload, (list, tuple, set)):
        return [safe_audit_payload(item, max_depth=max_depth - 1) for item in list(payload)[:40]]
    return _safe_scalar(payload)


def log_ai_decision_audit(
    *,
    event: str,
    request_id: str | None = None,
    tenant_key: str | None = None,
    channel_key: str | None = None,
    session_id: str | None = None,
    conversation_id: int | None = None,
    ticket_id: int | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    safe_payload = safe_audit_payload(payload or {})
    LOGGER.info(
        event,
        extra={
            "event_payload": {
                "runtime": "webchat_ai_decision_runtime",
                "request_id": request_id,
                "tenant_key": tenant_key,
                "channel_key": channel_key,
                "session_id_hash": _sha(session_id or "") if session_id else None,
                "conversation_id": conversation_id,
                "ticket_id": ticket_id,
                "payload": safe_payload,
            }
        },
    )


def stable_json_hash(payload: Any) -> str:
    safe = safe_audit_payload(payload)
    text = json.dumps(safe, ensure_ascii=False, sort_keys=True, default=str)
    return _sha(text)
