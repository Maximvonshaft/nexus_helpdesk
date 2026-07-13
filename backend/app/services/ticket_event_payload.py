from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

_MESSAGE_ID_RE = re.compile(
    r"^<[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,128}@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,118}[A-Za-z0-9])?>$"
)
_OPAQUE_PROVIDER_REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,254}$")
_SECRET_SHAPED_REFERENCE_RE = re.compile(
    r"(?:\bsk-(?:proj-)?[A-Za-z0-9_-]{12,}|"
    r"\bbearer(?:[._:+/-]|\s+)[A-Za-z0-9._~+/=-]{8,}|"
    r"\b(?:password|secret|api[_-]?key|credential|authorization|token)[:=_-][A-Za-z0-9._~+/=-]{8,})",
    re.IGNORECASE,
)
_MAX_REFERENCE_CHAIN_ITEMS = 10
_ALLOWED_REFERENCE_KEYS_BY_CLASS: dict[str, frozenset[str]] = {
    "customer_visible": frozenset(
        {
            "mailbox_thread_id",
            "mailbox_message_id",
            "mailbox_references",
            "provider_message_id",
        }
    ),
    "provider": frozenset(
        {
            "mailbox_thread_id",
            "mailbox_message_id",
            "mailbox_references",
            "provider_message_id",
        }
    ),
    "internal_audit": frozenset({"voice_session_id"}),
}


def _message_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if len(text) > 255 or not _MESSAGE_ID_RE.fullmatch(text):
        return None
    return text


def _message_id_chain(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    items = value.strip().split()
    if not items or len(items) > _MAX_REFERENCE_CHAIN_ITEMS:
        return None
    normalized = [_message_id(item) for item in items]
    if any(item is None for item in normalized):
        return None
    return " ".join(item for item in normalized if item is not None)


def _provider_reference(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > 255:
        return None
    if _SECRET_SHAPED_REFERENCE_RE.search(text):
        return None
    message_id = _message_id(text)
    if message_id is not None:
        return message_id
    if "@" in text or "<" in text or ">" in text:
        return None
    if not _OPAQUE_PROVIDER_REFERENCE_RE.fullmatch(text):
        return None
    return text


def prepare_ticket_event_payload(
    value: Any,
    *,
    event_class: str,
) -> tuple[Any, dict[str, str]]:
    """Separate narrowly approved operational references from untrusted payload.

    Full Email/Provider source records remain authoritative. TicketEvent may copy
    only bounded RFC Message-IDs/reference chains and opaque Provider message IDs
    for Customer-visible or Provider evidence. These values are not accepted by
    the generic identifier sanitizer and are merged only after the class-scoped
    payload has passed the normal recursive sanitization boundary.
    """

    if not isinstance(value, Mapping):
        return value, {}
    prepared = dict(value)
    approved: dict[str, str] = {}
    allowed = _ALLOWED_REFERENCE_KEYS_BY_CLASS.get(event_class, frozenset())
    for key in sorted(allowed):
        if key not in prepared:
            continue
        raw_value = prepared.pop(key)
        if key == "mailbox_references":
            normalized = _message_id_chain(raw_value)
        elif key.startswith("mailbox_"):
            normalized = _message_id(raw_value)
        else:
            # Provider and WebCall public references are server-owned opaque IDs.
            # Merge them only after recursive payload sanitization and only after
            # rejecting credential/token-shaped values that would bypass it.
            normalized = _provider_reference(raw_value)
        if normalized is not None:
            approved[key] = normalized
    return prepared, approved


def merge_ticket_event_operational_references(
    payload_json: str,
    references: Mapping[str, str],
    *,
    max_bytes: int,
) -> str:
    if not references:
        return payload_json
    try:
        payload = json.loads(payload_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return payload_json
    if not isinstance(payload, dict):
        return payload_json
    payload.update(dict(references))
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(encoded.encode("utf-8")) > max_bytes:
        return payload_json
    return encoded


__all__ = [
    "merge_ticket_event_operational_references",
    "prepare_ticket_event_payload",
]
