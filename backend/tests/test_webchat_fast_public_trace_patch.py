from __future__ import annotations

import os

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_fast_public_trace_patch_tests.db")

from app.api import webchat_fast_public_trace_patch as patch
from app.api import webchat_fast as webchat_fast


def setup_function():
    patch._NEGATIVE_CACHE.clear()


def test_public_payload_sanitizer_redacts_trace_identifiers_without_breaking_generic_format_guidance():
    payload = {
        "reply": "Please check the format is CH + 12 digits and retry.",
        "tracking_number_hash": "sha256:abc123",
        "tracking_number_suffix": "011425",
        "evidence_trace": {
            "query_analysis": {
                "normalized_query": "CH1200000011425 tracking lookup failed",
                "numeric_terms": ["1200000011425"],
                "high_value_terms": ["CH1200000011425", "tracking number format"],
            }
        },
    }

    sanitized = patch._sanitize_public_payload(payload)
    serialized = str(sanitized)

    assert "CH1200000011425" not in serialized
    assert "1200000011425" not in serialized
    assert sanitized["reply"] == "Please check the format is CH + 12 digits and retry."
    assert sanitized["tracking_number_hash"] == "sha256:abc123"
    assert sanitized["tracking_number_suffix"] == "011425"
    assert sanitized["evidence_trace"]["query_analysis"]["numeric_terms"] == ["tracking_number_redacted"]


def test_invalid_ch_waybill_format_precheck_blocks_external_lookup(monkeypatch):
    calls = {"count": 0}

    def fake_original_lookup(**_kwargs):
        calls["count"] += 1
        raise AssertionError("invalid CH waybill should not call external lookup")

    monkeypatch.setattr(patch, "_ORIGINAL_LOOKUP_FAST_TRACKING_FACT", fake_original_lookup)

    result = patch._lookup_fast_tracking_fact_guarded(
        tracking_number="CH1200000011425",
        conversation_id=1,
        ticket_id=None,
        request_id="test-invalid-format",
        caller_id=None,
        country_code="CH",
    )

    assert calls["count"] == 0
    assert result is not None
    assert result.fact_evidence_present is False
    assert result.pii_redacted is True
    assert result.tool_status == "format_invalid"
    assert result.failure_reason == "invalid_ch_waybill_format"


def test_negative_lookup_cache_uses_hash_key_and_reuses_non_transient_no_evidence(monkeypatch):
    calls = {"count": 0}

    def fake_original_lookup(**kwargs):
        calls["count"] += 1
        return webchat_fast.TrackingFactResult(
            ok=False,
            tracking_number=kwargs["tracking_number"],
            tool_status="error",
            pii_redacted=True,
            fact_evidence_present=False,
            failure_reason="1140003",
        )

    monkeypatch.setenv("WEBCHAT_FAST_TRACKING_NEGATIVE_CACHE_SECONDS", "60")
    monkeypatch.setattr(patch, "_ORIGINAL_LOOKUP_FAST_TRACKING_FACT", fake_original_lookup)

    first = patch._lookup_fast_tracking_fact_guarded(
        tracking_number="CH120000011425",
        conversation_id=1,
        ticket_id=None,
        request_id="test-negative-cache-1",
        caller_id=None,
        country_code="CH",
    )
    second = patch._lookup_fast_tracking_fact_guarded(
        tracking_number="CH120000011425",
        conversation_id=1,
        ticket_id=None,
        request_id="test-negative-cache-2",
        caller_id=None,
        country_code="CH",
    )

    assert calls["count"] == 1
    assert first is not None and first.failure_reason == "1140003"
    assert second is not None and second.failure_reason == "1140003"
    assert second.fact_evidence_present is False
    assert all("CH120000011425" not in str(item) for item in patch._NEGATIVE_CACHE.values())
