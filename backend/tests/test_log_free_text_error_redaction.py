from __future__ import annotations

import json

from app.services.log_sanitizer import build_safe_log_payload, sanitize_log_event


def test_free_form_error_fields_are_redacted_as_a_whole():
    secret_text = "customer said ZXCV-1234 and token=SHOULD_NOT_PERSIST"
    payload = sanitize_log_event(
        {
            "error": secret_text,
            "error_message": secret_text,
            "exception": secret_text,
            "error_type": "RuntimeError",
            "error_code": "provider_unavailable",
        }
    )
    rendered = json.dumps(payload, sort_keys=True)
    assert "SHOULD_NOT_PERSIST" not in rendered
    assert "ZXCV-1234" not in rendered
    assert payload["error"]["category"] == "free_text_error"
    assert payload["error_message"]["category"] == "free_text_error"
    assert payload["exception"]["category"] == "free_text_error"
    assert payload["error_type"] == "RuntimeError"
    assert payload["error_code"] == "provider_unavailable"


def test_top_level_log_payload_keeps_stable_diagnostics_only():
    payload = build_safe_log_payload(
        level="ERROR",
        logger="nexusdesk",
        message="request_failed",
        event_payload={
            "request_id": "request-123",
            "error": "email customer@example.test password=SHOULD_NOT_PERSIST",
            "error_type": "ValueError",
        },
    )
    rendered = json.dumps(payload, sort_keys=True)
    assert "customer@example.test" not in rendered
    assert "SHOULD_NOT_PERSIST" not in rendered
    assert payload["error_type"] == "ValueError"
    assert payload["error"]["redacted"] is True
