from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .nexus_osr.audit_sanitizer import (
    AuditSanitizerLimits,
    safe_audit_label,
    sanitize_audit_payload,
)

TICKET_EVENT_CONTRACT = "nexus.ticket_event.writer.v1"
MAX_TICKET_EVENT_BYTES = 8 * 1024
_DEFAULT_EVENT_LIMITS = AuditSanitizerLimits(
    max_depth=6,
    max_mapping_items=64,
    max_sequence_items=32,
    max_string_length=240,
    max_key_length=80,
)
_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_DEFAULT_SAFE_IDENTIFIER_KEYS = frozenset(
    {
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
)
_DEFAULT_SAFE_LABEL_KEYS = frozenset(
    {
        "action",
        "allowed",
        "category",
        "channel",
        "code",
        "country_code",
        "created",
        "current_status",
        "error_code",
        "external_send",
        "failure_code",
        "failure_reason_code",
        "handoff_required",
        "message_type",
        "next_action",
        "origin",
        "outcome",
        "policy_key",
        "provider",
        "provider_status",
        "reason_code",
        "reply_channel",
        "risk_level",
        "route_status",
        "source",
        "status",
        "tool_name",
        "trigger_type",
    }
)
_STRUCTURAL_KEYS = frozenset(
    {
        "category",
        "event_class",
        "event_contract",
        "present",
        "redacted",
        "redacted_field_count",
        "redaction_categories",
        "schema_version",
        "sha256_prefix",
    }
)


@dataclass(frozen=True)
class TicketEventPayloadPolicy:
    event_class: str
    safe_identifier_keys: frozenset[str]
    safe_label_keys: frozenset[str]
    safe_structured_keys: frozenset[str] = frozenset()
    schema_version: int = 1
    contract: str = TICKET_EVENT_CONTRACT
    limits: AuditSanitizerLimits = _DEFAULT_EVENT_LIMITS
    max_bytes: int = MAX_TICKET_EVENT_BYTES


DEFAULT_TICKET_EVENT_POLICY = TicketEventPayloadPolicy(
    event_class="customer_visible",
    safe_identifier_keys=_DEFAULT_SAFE_IDENTIFIER_KEYS,
    safe_label_keys=_DEFAULT_SAFE_LABEL_KEYS,
)


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


def _safe_identifiers(
    value: Mapping[str, Any], keys: frozenset[str]
) -> dict[str, int | str]:
    result: dict[str, int | str] = {}
    for key in sorted(keys):
        if key not in value:
            continue
        normalized = _safe_identifier(value.get(key))
        if normalized is not None:
            result[key] = normalized
    return result


def _safe_policy_value(
    key: str, value: Any, *, limits: AuditSanitizerLimits
) -> bool | int | float | str | None:
    sanitized = sanitize_audit_payload(
        {key: value},
        limits=AuditSanitizerLimits(
            max_depth=2,
            max_mapping_items=4,
            max_sequence_items=4,
            max_string_length=limits.max_string_length,
            max_key_length=limits.max_key_length,
        ),
    )
    if not isinstance(sanitized, dict):
        return None
    candidate = sanitized.get(key)
    if candidate is None or isinstance(candidate, bool):
        return candidate
    if isinstance(candidate, int) and not isinstance(candidate, bool):
        return candidate
    if isinstance(candidate, float):
        return candidate
    if isinstance(candidate, str):
        label = safe_audit_label(
            candidate, fallback="", max_length=limits.max_string_length
        )
        return label or None
    return None


def _collect_redaction_categories(value: Any, *, limit: int = 16) -> list[str]:
    categories: set[str] = set()

    def visit(item: Any) -> None:
        if len(categories) >= limit:
            return
        if isinstance(item, Mapping):
            if item.get("redacted") is True:
                category = safe_audit_label(
                    item.get("category"), fallback="redacted", max_length=64
                )
                categories.add(category or "redacted")
            for child in item.values():
                visit(child)
        elif isinstance(item, Sequence) and not isinstance(
            item, (str, bytes, bytearray, memoryview)
        ):
            for child in item:
                visit(child)

    visit(value)
    return sorted(categories)[:limit]


def _canonical_bytes(value: Any) -> bytes | None:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError):
        return None


def _fallback_payload(
    *,
    policy: TicketEventPayloadPolicy,
    category: str,
    encoded: bytes | None,
    identifiers: dict[str, int | str],
) -> dict[str, Any]:
    marker: dict[str, Any] = {
        "event_contract": policy.contract,
        "event_class": policy.event_class,
        "schema_version": policy.schema_version,
        "redacted": True,
        "category": category,
        "present": True,
    }
    marker.update(identifiers)
    if encoded:
        marker["sha256_prefix"] = hashlib.sha256(encoded).hexdigest()[:16]
    return marker


def _safe_redaction_category(value: Any) -> str | None:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")[:64]
    if not normalized:
        return None
    return safe_audit_label(normalized, fallback="", max_length=64) or None


def sanitize_ticket_event_payload(
    value: Any,
    *,
    policy: TicketEventPayloadPolicy | None = None,
) -> dict[str, Any]:
    """Return one class-scoped, bounded and JSON-safe TicketEvent payload.

    Caller content is always treated as untrusted. The recursive Audit Sanitizer
    detects hostile structures and oversized data, while this function persists
    only the event-class allowlist and injects immutable contract metadata.
    """

    resolved_policy = policy or DEFAULT_TICKET_EVENT_POLICY
    sanitized = sanitize_audit_payload(value, limits=resolved_policy.limits)
    unfiltered_encoded = _canonical_bytes(sanitized)
    if unfiltered_encoded is None:
        return _fallback_payload(
            policy=resolved_policy,
            category="event_payload_invalid",
            encoded=None,
            identifiers={},
        )

    if not isinstance(value, Mapping):
        category = "event_payload_invalid"
        if isinstance(sanitized, Mapping):
            category = _safe_redaction_category(sanitized.get("category")) or category
        return _fallback_payload(
            policy=resolved_policy,
            category=category,
            encoded=unfiltered_encoded,
            identifiers={},
        )

    raw_mapping: Mapping[str, Any] = value
    identifiers = _safe_identifiers(raw_mapping, resolved_policy.safe_identifier_keys)
    if len(unfiltered_encoded) > resolved_policy.max_bytes:
        return _fallback_payload(
            policy=resolved_policy,
            category="event_payload_too_large",
            encoded=unfiltered_encoded,
            identifiers=identifiers,
        )

    already_governed = (
        raw_mapping.get("event_contract") == resolved_policy.contract
        and raw_mapping.get("event_class") == resolved_policy.event_class
        and raw_mapping.get("schema_version") == resolved_policy.schema_version
    )

    result: dict[str, Any] = {
        "event_contract": resolved_policy.contract,
        "event_class": resolved_policy.event_class,
        "schema_version": resolved_policy.schema_version,
    }
    result.update(identifiers)

    invalid_allowed_count = 0
    for key in sorted(resolved_policy.safe_identifier_keys):
        if key in raw_mapping and key not in identifiers:
            invalid_allowed_count += 1

    for key in sorted(resolved_policy.safe_label_keys):
        if key not in raw_mapping:
            continue
        normalized = _safe_policy_value(
            key, raw_mapping.get(key), limits=resolved_policy.limits
        )
        if normalized is not None:
            result[key] = normalized
        else:
            invalid_allowed_count += 1

    for key in sorted(resolved_policy.safe_structured_keys):
        if key not in raw_mapping:
            continue
        raw_value = raw_mapping.get(key)
        if not isinstance(raw_value, (Mapping, Sequence)) or isinstance(
            raw_value, (str, bytes, bytearray, memoryview)
        ):
            invalid_allowed_count += 1
            continue
        structured = sanitize_audit_payload(raw_value, limits=resolved_policy.limits)
        if isinstance(structured, (dict, list)):
            result[key] = structured
        else:
            invalid_allowed_count += 1

    allowed_keys = (
        resolved_policy.safe_identifier_keys
        | resolved_policy.safe_label_keys
        | resolved_policy.safe_structured_keys
        | _STRUCTURAL_KEYS
    )
    filtered_keys = [str(key) for key in raw_mapping if str(key) not in allowed_keys]

    if raw_mapping.get("redacted") is True:
        result["redacted"] = True
        raw_category = (
            _safe_redaction_category(raw_mapping.get("category")) or "redacted"
        )
        result["category"] = raw_category
        if raw_mapping.get("present") is not None:
            result["present"] = bool(raw_mapping.get("present"))
        raw_hash = _safe_identifier(raw_mapping.get("sha256_prefix"))
        if isinstance(raw_hash, str):
            result["sha256_prefix"] = raw_hash[:16]

    if already_governed:
        raw_count = raw_mapping.get("redacted_field_count")
        if (
            isinstance(raw_count, int)
            and not isinstance(raw_count, bool)
            and raw_count > 0
        ):
            result["redacted_field_count"] = min(raw_count, 1_000_000)
        raw_categories = raw_mapping.get("redaction_categories")
        if isinstance(raw_categories, Sequence) and not isinstance(
            raw_categories, (str, bytes, bytearray, memoryview)
        ):
            categories = sorted(
                {
                    category
                    for item in list(raw_categories)[:16]
                    if (category := _safe_redaction_category(item)) is not None
                }
            )
            if categories:
                result["redaction_categories"] = categories
    else:
        redacted_count = len(filtered_keys) + invalid_allowed_count
        if redacted_count:
            result["redacted"] = True
            result["redacted_field_count"] = min(redacted_count, 1_000_000)
            categories: list[str] = []
            for key in filtered_keys[:16]:
                source = sanitized.get(key) if isinstance(sanitized, Mapping) else None
                source_category = (
                    source.get("category")
                    if isinstance(source, Mapping) and source.get("redacted") is True
                    else key
                )
                category = _safe_redaction_category(source_category)
                if category is not None:
                    categories.append(category)
            categories = sorted(set(categories))
            if categories:
                result["redaction_categories"] = categories
            result.setdefault("category", "policy_filtered")
            result.setdefault("present", True)

    encoded = _canonical_bytes(result)
    if encoded is None:
        return _fallback_payload(
            policy=resolved_policy,
            category="event_payload_invalid",
            encoded=None,
            identifiers=identifiers,
        )
    if len(encoded) > resolved_policy.max_bytes:
        return _fallback_payload(
            policy=resolved_policy,
            category="event_payload_too_large",
            encoded=encoded,
            identifiers=identifiers,
        )
    return result


def serialize_ticket_event_payload(
    value: Any,
    *,
    policy: TicketEventPayloadPolicy | None = None,
) -> str:
    payload = sanitize_ticket_event_payload(value, policy=policy)
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sanitize_ticket_event_text(value: Any, *, limit: int) -> str | None:
    if value is None:
        return None
    limits = AuditSanitizerLimits(
        max_depth=2,
        max_mapping_items=4,
        max_sequence_items=4,
        max_string_length=limit,
        max_key_length=80,
    )
    sanitized = sanitize_audit_payload({"event_text": value}, limits=limits)
    candidate = sanitized.get("event_text") if isinstance(sanitized, dict) else None
    if isinstance(candidate, str):
        return candidate or None
    if isinstance(candidate, Mapping):
        category = safe_audit_label(
            candidate.get("category"), fallback="redacted", max_length=64
        )
        return f"[redacted:{category or 'redacted'}]"[:limit]
    return "[redacted:event_text_invalid]"[:limit]


def sanitize_ticket_event_field_name(value: Any) -> str | None:
    if value is None:
        return None
    normalized = safe_audit_label(value, fallback="", max_length=120)
    return normalized or None
