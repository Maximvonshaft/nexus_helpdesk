from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app.services.speedaf.tracking_truth_source import (
    lookup_speedaf_contract_safe_hybrid_tracking_fact,
    merge_contract_safe_hybrid_tracking_fact,
)
from app.services.tracking_fact_schema import TrackingFactEvent, TrackingFactResult
from app.services.tracking_truth_contract import (
    AUTHORITY_ENRICHMENT,
    AUTHORITY_PRIMARY,
    EVIDENCE_CONTRADICTORY,
    EVIDENCE_NO_EVIDENCE,
    EVIDENCE_STALE,
    EVIDENCE_TIMEOUT,
    EVIDENCE_UNAVAILABLE,
)


def _primary(*, evidence: bool = True) -> TrackingFactResult:
    return TrackingFactResult(
        ok=evidence,
        tracking_number="CH120000005451",
        status="4" if evidence else None,
        status_label="At delivery station" if evidence else None,
        latest_event=TrackingFactEvent(description="At delivery station", event_time="2026-07-10T08:00:00+00:00") if evidence else None,
        events_summary=[],
        checked_at="2026-07-10T08:01:00+00:00",
        source="speedaf_api.order_query",
        tool_name="speedaf.order.query",
        tool_status="success" if evidence else "not_found",
        pii_redacted=True,
        fact_evidence_present=evidence,
        failure_reason=None if evidence else "tracking_lookup_no_match",
    )


def _history(
    *,
    status: str | None = "5",
    event_time: str = "2026-07-10T08:30:00+00:00",
    evidence: bool = True,
    failure_reason: str | None = None,
) -> TrackingFactResult:
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


def test_primary_current_status_is_preserved_when_history_contradicts() -> None:
    result = merge_contract_safe_hybrid_tracking_fact(
        primary=_primary(),
        history=_history(status="5"),
        now=datetime(2026, 7, 10, 9, tzinfo=timezone.utc),
    )

    assert result.status == "4"
    assert result.status_label == "At delivery station"
    assert result.latest_event.description == "At delivery station"
    assert result.evidence_state == EVIDENCE_CONTRADICTORY
    assert result.contradictions[0]["resolution"] == "primary_current_status_preserved"
    assert result.used_sources[0]["authority"] == AUTHORITY_PRIMARY
    assert result.used_sources[1]["authority"] == AUTHORITY_ENRICHMENT


def test_stale_history_is_explicit_and_does_not_replace_primary() -> None:
    result = merge_contract_safe_hybrid_tracking_fact(
        primary=_primary(),
        history=_history(event_time="2026-06-01T08:30:00+00:00", status="4"),
        now=datetime(2026, 7, 10, 9, tzinfo=timezone.utc),
        stale_after_seconds=3600,
    )

    assert result.status == "4"
    assert result.used_sources[1]["evidence_state"] == EVIDENCE_STALE
    assert result.used_sources[1]["freshness"] == "stale"


def test_timeout_and_no_evidence_history_are_explicit() -> None:
    timeout = merge_contract_safe_hybrid_tracking_fact(
        primary=_primary(),
        history=_history(evidence=False, failure_reason="timeout"),
    )
    no_evidence = merge_contract_safe_hybrid_tracking_fact(
        primary=_primary(),
        history=_history(evidence=False, failure_reason="no_events"),
    )

    assert timeout.status == "4"
    assert timeout.used_sources[1]["evidence_state"] == EVIDENCE_TIMEOUT
    assert no_evidence.status == "4"
    assert no_evidence.used_sources[1]["evidence_state"] == EVIDENCE_NO_EVIDENCE


def test_primary_without_evidence_never_queries_history(monkeypatch) -> None:
    primary = _primary(evidence=False)
    calls = []

    monkeypatch.setattr(
        "app.services.speedaf.tracking_truth_source.lookup_speedaf_tracking_fact",
        lambda **_kwargs: primary,
    )
    monkeypatch.setattr(
        "app.services.speedaf.tracking_truth_source.lookup_speedaf_track_history_fact",
        lambda **_kwargs: calls.append(True),
    )

    result = lookup_speedaf_contract_safe_hybrid_tracking_fact(
        tracking_number="CH120000005451",
        track_client=SimpleNamespace(config=SimpleNamespace(configured=True)),
    )

    assert result.fact_evidence_present is False
    assert result.status is None
    assert calls == []


def test_unavailable_history_is_reported_without_losing_primary(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.speedaf.tracking_truth_source.lookup_speedaf_tracking_fact",
        lambda **_kwargs: _primary(),
    )

    result = lookup_speedaf_contract_safe_hybrid_tracking_fact(
        tracking_number="CH120000005451",
        track_client=SimpleNamespace(config=SimpleNamespace(configured=False)),
    )

    assert result.status == "4"
    assert result.fact_evidence_present is True
    assert result.used_sources[1]["evidence_state"] == EVIDENCE_UNAVAILABLE


def test_tracking_metadata_redacts_raw_identifiers_credentials_and_provider_payloads() -> None:
    result = merge_contract_safe_hybrid_tracking_fact(
        primary=TrackingFactResult(
            ok=True,
            tracking_number="CH120000005451",
            status="4",
            checked_at="2026-07-10T08:01:00+00:00",
            source="speedaf_api.order_query",
            tool_name="speedaf.order.query",
            tool_status="success",
            pii_redacted=True,
            fact_evidence_present=True,
            status_context={
                "safe_code": "4",
                "secret_key": "do-not-log",
                "tracking_number": "CH120000005451",
                "provider_payload": {"recipient": "sensitive"},
            },
        ),
        history=_history(evidence=False, failure_reason="no_events"),
    )

    payload = result.metadata_payload()
    serialized = repr(payload)

    assert payload["tracking_number_hash"].startswith("sha256:")
    assert payload["source_authority"] == AUTHORITY_PRIMARY
    assert "do-not-log" not in serialized
    assert "CH120000005451" not in serialized
    assert "recipient" not in serialized
