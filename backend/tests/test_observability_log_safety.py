from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.log_sanitizer import build_safe_log_payload, sanitize_log_event  # noqa: E402
from app.services.observability import _JsonFormatter, _SafeTextFormatter  # noqa: E402


SENSITIVE_VALUES = (
    "customer@example.test",
    "+41790000000",
    "CH0200001291399",
    "Bearer super-secret-token-value",
    "12 Example Street",
    "123456789012345@g.us",
)


def _assert_redacted(serialized: str) -> None:
    for value in SENSITIVE_VALUES:
        assert value not in serialized


def test_build_safe_log_payload_redacts_nested_values_and_sensitive_keys() -> None:
    payload = build_safe_log_payload(
        level="INFO",
        logger="nexusdesk",
        message="provider failed for customer@example.test CH0200001291399",
        event_payload={
            "phone": "+41790000000",
            "tracking_number": "CH0200001291399",
            "nested": {
                "note": "Bearer super-secret-token-value at 12 Example Street",
                "group": "123456789012345@g.us",
            },
        },
    )

    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    _assert_redacted(serialized)
    assert payload["level"] == "INFO"
    assert payload["logger"] == "nexusdesk"
    assert "redacted" in serialized.lower()


def test_json_formatter_sanitizes_terminal_record_and_reserved_collisions() -> None:
    record = logging.LogRecord(
        name="nexusdesk",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="request failed for %s",
        args=("customer@example.test",),
        exc_info=None,
    )
    record.event_payload = {
        "level": "attacker-level",
        "logger": "attacker-logger",
        "message": "Bearer super-secret-token-value",
        "customer": "+41790000000 CH0200001291399",
    }

    output = _JsonFormatter().format(record)
    parsed = json.loads(output)

    _assert_redacted(output)
    assert parsed["level"] == "ERROR"
    assert parsed["logger"] == "nexusdesk"
    assert "event_level" in parsed
    assert "event_logger" in parsed
    assert "event_message" in parsed


def test_text_formatter_uses_the_same_terminal_safety_boundary() -> None:
    record = logging.LogRecord(
        name="nexusdesk",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="outbound error customer@example.test",
        args=(),
        exc_info=None,
    )
    record.event_payload = {"detail": "CH0200001291399 +41790000000"}

    output = _SafeTextFormatter().format(record)

    _assert_redacted(output)
    assert output.startswith("WARNING nexusdesk")


def test_cycles_and_exceptions_fail_closed_without_formatter_error() -> None:
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic
    cyclic["exception"] = RuntimeError("Bearer super-secret-token-value")

    sanitized = sanitize_log_event(cyclic)
    serialized = json.dumps(sanitized, ensure_ascii=False, sort_keys=True)

    _assert_redacted(serialized)
    assert "cycle" in serialized
    assert "exception" in serialized


def test_unsupported_message_string_failure_returns_constant_fallback() -> None:
    class BrokenString:
        def __str__(self) -> str:
            raise RuntimeError("customer@example.test")

    record = logging.LogRecord(
        name="nexusdesk",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg=BrokenString(),
        args=(),
        exc_info=None,
    )

    output = _JsonFormatter().format(record)

    assert json.loads(output)["message"] == "log_formatter_failure"
    _assert_redacted(output)
