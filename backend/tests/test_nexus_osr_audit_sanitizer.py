from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from enum import StrEnum
import json
import math

from app.services.nexus_osr.audit_sanitizer import (
    AuditSanitizerLimits,
    safe_audit_label,
    sanitize_audit_payload,
)


class _Status(StrEnum):
    READY = "ready"


class _HostileObject:
    def __repr__(self) -> str:  # pragma: no cover - the sanitizer must never persist this
        return "HostileObject(sk-proj-REPRSECRET123456789)"


def _serialized(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def test_sanitizer_redacts_hostile_nested_payload_and_preserves_safe_metadata():
    raw_tracking = "CH1234567890"
    raw_phone = "+382 67123456"
    raw_email = "audit@example.test"
    raw_secret = "sk-proj-AUDITSECRET123456789"
    raw_group = "120363777777777777@g.us"
    raw_address = "221 Baker Street"
    payload = {
        "authority": "mcp",
        "source_type": "order_query",
        "policy_key": "tracking.current_status",
        "status": _Status.READY,
        "observed_at": datetime(2026, 7, 10, 10, 0, tzinfo=timezone.utc),
        "confidence": 0.95,
        "verified": True,
        "provider_payload": {"authorization": f"Bearer {raw_secret}"},
        "tool_arguments": {"tracking_number": raw_tracking, "phone": raw_phone},
        "tool_result": {"email": raw_email, "address": raw_address},
        "destination_group_id": raw_group,
        "summary": {
            "status": "out_for_delivery",
            "note": f"Contact {raw_email} at {raw_phone} for {raw_tracking} from {raw_address}",
        },
        "nested": [{"password": "P@ssw0rd!", "count": 2}],
        "tuple_value": (1, "safe"),
        "set_value": {"b", "a"},
        "exception": ValueError(f"provider failed for {raw_tracking}"),
        "object": _HostileObject(),
        "nan": math.nan,
    }
    original = deepcopy({key: value for key, value in payload.items() if key not in {"exception", "object"}})

    sanitized = sanitize_audit_payload(payload)
    text = _serialized(sanitized)

    for raw in (raw_tracking, raw_phone, raw_email, raw_secret, raw_group, raw_address, "P@ssw0rd!", "REPRSECRET"):
        assert raw not in text
    assert sanitized["authority"] == "mcp"
    assert sanitized["source_type"] == "order_query"
    assert sanitized["policy_key"] == "tracking.current_status"
    assert sanitized["status"] == "ready"
    assert sanitized["verified"] is True
    assert sanitized["provider_payload"]["redacted"] is True
    assert sanitized["tool_arguments"]["redacted"] is True
    assert sanitized["destination_group_id"]["redacted"] is True
    assert sanitized["exception"]["category"] == "exception"
    assert sanitized["object"]["category"] == "unsupported_object"
    assert sanitized["nan"]["category"] == "non_finite_number"
    assert payload["authority"] == original["authority"]
    assert payload["summary"] == original["summary"]


def test_sanitizer_is_deterministic_bounded_and_cycle_safe():
    cyclic: dict[str, object] = {"status": "active"}
    cyclic["self"] = cyclic
    payload = {
        "cycle": cyclic,
        "many": list(range(20)),
        "mapping": {f"key_{index}": index for index in range(20)},
        "long": "x" * 500,
    }
    limits = AuditSanitizerLimits(
        max_depth=4,
        max_mapping_items=5,
        max_sequence_items=4,
        max_string_length=60,
        max_key_length=40,
    )

    first = sanitize_audit_payload(payload, limits=limits)
    second = sanitize_audit_payload(payload, limits=limits)

    assert first == second
    assert first["cycle"]["self"]["category"] == "cycle"
    assert first["many"][-1]["__truncated_items__"] == 16
    assert first["mapping"]["__truncated_keys__"] == 15
    assert len(first["long"]) <= 60


def test_sanitizer_redacts_sensitive_keys_and_key_names():
    raw_email_key = "person@example.test"
    result = sanitize_audit_payload({
        raw_email_key: "safe",
        "tracking_number_hash": "abc123hash",
        "safe_tracking_reference": "***7890",
        "provider_group_id": "120363999999999999@g.us",
    })
    text = _serialized(result)

    assert raw_email_key not in text
    assert result["tracking_number_hash"] == "abc123hash"
    assert result["safe_tracking_reference"] == "***7890"
    assert result["provider_group_id"]["redacted"] is True


def test_safe_audit_label_rejects_free_form_and_pii():
    assert safe_audit_label("tracking_status_answer", fallback="unknown") == "tracking_status_answer"
    assert safe_audit_label("audit@example.test", fallback="unknown") == "unknown"
    assert safe_audit_label("free form text", fallback="unknown") == "unknown"
