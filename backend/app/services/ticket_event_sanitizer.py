from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

from .nexus_osr.audit_sanitizer import (
    AuditSanitizerLimits,
    safe_audit_label,
    sanitize_audit_payload,
)

MAX_TICKET_EVENT_BYTES = 8 * 1024
_EVENT_LIMITS = AuditSanitizerLimits(
    max_depth=6,
    max_mapping_items=64,
    max_sequence_items=32,
    max_string_length=240,
    max_key_length=80,
)
_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SAFE_IDENTIFIER_KEYS = {
    "actor_id",
    "audit_id",
    "case_context_id",
    "comment_id",
    "conversation_id",
    "conversation_public_id",
    "dispatch_outbox_id",
    "event_id",
    "handoff_request_id",
    "message_id",
    "operator_task_id",
    "outbound_message_id",
    "reply_to_message_id",
    "routing_rule_id",
    "ticket_id",
    "tool_call_log_id",
    "user_id",
    "webchat_message_id",
}


def _safe_identifier(value: Any) -> int | str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if 0 <= value <= 2**63 - 1 else None
    if isinstance(value, str):
        text = value.strip()
        if not _SAFE_IDENTIFIER_RE.fullmatch(text):
            return None
        safe = safe_audit_label(text, fallback="", max_length=128)
        return safe or None
    return None


def _safe_identifiers(value: Mapping[str, Any]) -> dict[str, int | str]:
    result: dict[str, int | str] = {}
    for key in sorted(_SAFE_IDENTIFIER_KEYS):
        if key not in value:
            continue
        normalized = _safe_identifier(value.get(key))
        if normalized is not None:
            result[key] = normalized
    return result


def _fallback_payload(*, category: str, encoded: bytes | None, identifiers: dict[str, int | str]) -> dict[str, Any]:
    marker: dict[str, Any] = {
        "redacted": True,
        "category": category,
        "present": True,
    }
    marker.update(identifiers)
    if encoded:
        marker["sha256_prefix"] = hashlib.sha256(encoded).hexdigest()[:16]
    return marker


def sanitize_ticket_event_payload(value: Any) -> dict[str, Any]:
    """Return one bounded, JSON-safe payload for durable TicketEvent storage.

    The sanitizer is deliberately fail closed. Caller-provided payloads never
    bypass the recursive Audit Sanitizer. A small allow-list restores only
    operational identifiers that are required to join the timeline safely.
    """

    raw_mapping = value if isinstance(value, Mapping) else {}
    identifiers = _safe_identifiers(raw_mapping)
    sanitized = sanitize_audit_payload(value, limits=_EVENT_LIMITS)
    if not isinstance(sanitized, dict):
        return _fallback_payload(
            category="event_payload_invalid",
            encoded=None,
            identifiers=identifiers,
        )
    sanitized.update(identifiers)
    try:
        encoded = json.dumps(
            sanitized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError):
        return _fallback_payload(
            category="event_payload_invalid",
            encoded=None,
            identifiers=identifiers,
        )
    if len(encoded) > MAX_TICKET_EVENT_BYTES:
        return _fallback_payload(
            category="event_payload_too_large",
            encoded=encoded,
            identifiers=identifiers,
        )
    return sanitized


def serialize_ticket_event_payload(value: Any) -> str:
    payload = sanitize_ticket_event_payload(value)
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
