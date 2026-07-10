from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from app.services import tracking_fact_service
from app.services.speedaf.tracking_fact_source import merge_speedaf_hybrid_tracking_fact
from app.services.tracking_fact_schema import (
    EVIDENCE_CONTRADICTORY,
    EVIDENCE_NO_EVIDENCE,
    EVIDENCE_STALE,
    EVIDENCE_UNAVAILABLE,
    SOURCE_AUTHORITY_ENRICHMENT,
    SOURCE_AUTHORITY_NONE,
    SOURCE_AUTHORITY_PRIMARY,
    TrackingFactEvent,
    TrackingFactResult,
    as_tracking_truth_result,
)


def _primary(*, evidence: bool = True) -> TrackingFactResult:
    return TrackingFactResult(
        ok=evidence,
        tracking_number="CH120000005451",
        status="4" if evidence else None,
        status_label="At delivery station" if evidence else None,
        latest_event=TrackingFactEvent(description="At delivery station", event_time="2026-07-10T08:00:00+00:00") if evidence else None,
        checked_at="2026-07-10T08:01:00+00:00",
        source="speedaf_api.order_query",
        tool_name="speedaf.order.query",
        tool_status="success" if evidence else "not_found",
        pii_redacted=True,
        fact_evidence_present=evidence,
        failure_reason=None if evidence else "tracking_lookup_no_match",
    )


def _history(*, status: str | None = "5", event_time: str = "2026-07-10T08:30:00+00:00", evidence: bool = True, failure_reason: str | None = None) -> TrackingFactResult:
    return TrackingFactResult(
        ok=evidence,
        tracking_number="CH120000005451",
        status=status,
        status_label="Delivered" if status == "5" else status,
        latest_event=TrackingFactEvent(description="Delivered", event_time=event_time) if evidence else None,
        events_summary=[TrackingFactEvent(description="Delivered", event_time=event_time)] if evidence else [],
        checked_at="2026-07-10T08:31:00+00:00",
        source="speedaf_api.express_track_query",
        tool_name="speedaf.express.track.query",
        tool_status="success" if evidence else "error",
        pii_redacted=True,
        fact_evidence_present=evidence,
        failure_reason=failure_reason,
    )


def test_primary_preservation_contradiction_and_source_metadata() -> None:
    result = merge_speedaf_hybrid_tracking_fact(
        primary=_primary(),
        history=_history(status="5"),
        now=datetime(2026, 7, 10, 9, tzinfo=timezone.utc),
    )

    assert result.status == "4"
    assert result.latest_event.description == "At delivery station"
    assert result.evidence_state == EVIDENCE_CONTRADICTORY
    assert result.observed_at == "2026-07-10T08:00:00+00:00"
    assert result.freshness == "fresh"
    assert result.used_sources[0]["authority"] == SOURCE_AUTHORITY_PRIMARY
    assert result.used_sources[1]["authority"] == SOURCE_AUTHORITY_ENRICHMENT


def test_stale_history_is_explicit_and_does_not_replace_primary() -> None:
    result = merge_speedaf_hybrid_tracking_fact(
        primary=_primary(),
        history=_history(status="4", event_time="2026-06-01T08:30:00+00:00"),
        now=datetime(2026, 7, 10, 9, tzinfo=timezone.utc),
        stale_after_seconds=3600,
    )

    assert result.status == "4"
    assert result.used_sources[1]["evidence_state"] == EVIDENCE_STALE
    assert result.used_sources[1]["freshness"] == "stale"


def test_primary_unavailable_and_no_evidence_are_structured() -> None:
    unavailable = as_tracking_truth_result(
        TrackingFactResult(ok=False, tool_status="error", failure_reason="network_unavailable", pii_redacted=True),
        authority=SOURCE_AUTHORITY_PRIMARY,
    )
    no_evidence = as_tracking_truth_result(_primary(evidence=False), authority=SOURCE_AUTHORITY_PRIMARY)

    assert unavailable.evidence_state == EVIDENCE_UNAVAILABLE
    assert no_evidence.evidence_state == EVIDENCE_NO_EVIDENCE
    assert no_evidence.status is None


def test_history_only_service_result_never_represents_current_status(monkeypatch) -> None:
    monkeypatch.setattr(
        tracking_fact_service,
        "settings",
        SimpleNamespace(
            webchat_tracking_fact_source="speedaf_track_query",
            webchat_tracking_fact_lookup_enabled=True,
            webchat_tracking_fact_timeout_seconds=8,
        ),
    )
    monkeypatch.setattr(tracking_fact_service, "lookup_speedaf_track_history_fact", lambda **_kwargs: _history())

    result = tracking_fact_service.lookup_tracking_fact(tracking_number="CH120000005451")

    assert result.status is None
    assert result.fact_evidence_present is False
    assert result.source_authority == SOURCE_AUTHORITY_NONE
    assert result.used_sources[0]["authority"] == SOURCE_AUTHORITY_ENRICHMENT


def test_hybrid_has_independent_default_off_gate(monkeypatch) -> None:
    monkeypatch.delenv("WEBCHAT_TRACKING_HYBRID_ENABLED", raising=False)
    monkeypatch.setattr(
        tracking_fact_service,
        "settings",
        SimpleNamespace(
            webchat_tracking_fact_source="speedaf_hybrid",
            webchat_tracking_fact_lookup_enabled=True,
            webchat_tracking_fact_timeout_seconds=8,
        ),
    )
    calls = []
    monkeypatch.setattr(tracking_fact_service, "lookup_speedaf_hybrid_tracking_fact", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(tracking_fact_service, "record_tool_call", lambda **_kwargs: None)

    result = tracking_fact_service.lookup_tracking_fact(tracking_number="CH120000005451")

    assert result.failure_reason == "speedaf_hybrid_gate_disabled"
    assert result.evidence_state == EVIDENCE_UNAVAILABLE
    assert calls == []
    assert "speedaf_hybrid" in tracking_fact_service.TRACKING_FACT_SOURCE_ALLOWLIST


def test_tracking_audit_redacts_raw_identifier_credentials_and_provider_payload(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(tracking_fact_service, "record_tool_call", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(
        tracking_fact_service,
        "settings",
        SimpleNamespace(webchat_tracking_fact_source="speedaf_api"),
    )

    tracking_fact_service._audit_tracking_lookup(
        tracking_number="CH120000005451",
        conversation_id="not-a-safe-id",
        ticket_id="22",
        request_id="request-safe",
        status="failed",
        output_payload={
            "tracking_number": "CH120000005451",
            "secret_key": "credential-value",
            "provider_payload": {"recipient": "Jane"},
            "safe": "bounded",
        },
        error_code="provider_failure",
        error_message="raw backend output CH120000005451 credential-value",
    )

    serialized = repr(calls[0])
    assert "CH120000005451" not in serialized
    assert "credential-value" not in serialized
    assert "recipient" not in serialized
    assert calls[0]["input_payload"]["waybill_hash"].startswith("sha256:")
    assert calls[0]["error_message"] == "provider_failure"


def test_tracking_scope_has_no_customer_visible_dispatch_import() -> None:
    root = Path(__file__).resolve().parents[1] / "app" / "services"
    combined = "\n".join(
        (root / relative).read_text(encoding="utf-8")
        for relative in (
            "tracking_fact_schema.py",
            "tracking_fact_service.py",
            "speedaf/tracking_fact_source.py",
        )
    )
    assert "CustomerVisibleMessageService" not in combined
    assert "send_customer" not in combined
    assert "outbound_dispatch" not in combined
