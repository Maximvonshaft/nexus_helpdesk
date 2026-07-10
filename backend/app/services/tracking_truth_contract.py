from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any

from .tracking_fact_schema import TrackingFactResult

EVIDENCE_AVAILABLE = "available"
EVIDENCE_NO_EVIDENCE = "no_evidence"
EVIDENCE_STALE = "stale"
EVIDENCE_TIMEOUT = "timeout"
EVIDENCE_UNAVAILABLE = "unavailable"
EVIDENCE_CONTRADICTORY = "contradictory"
EVIDENCE_STATES = {
    EVIDENCE_AVAILABLE,
    EVIDENCE_NO_EVIDENCE,
    EVIDENCE_STALE,
    EVIDENCE_TIMEOUT,
    EVIDENCE_UNAVAILABLE,
    EVIDENCE_CONTRADICTORY,
}
AUTHORITY_PRIMARY = "primary_current_status"
AUTHORITY_ENRICHMENT = "history_enrichment"
AUTHORITY_NONE = "none"
AUTHORITIES = {AUTHORITY_PRIMARY, AUTHORITY_ENRICHMENT, AUTHORITY_NONE}

_SAFE_IDENTIFIER_KEYS = {
    "tracking_number_hash",
    "tracking_reference_suffix",
    "safe_tracking_reference",
    "waybill_hash",
    "waybill_suffix",
}
_SENSITIVE_KEY_PARTS = (
    "credential",
    "secret",
    "token",
    "password",
    "authorization",
    "cookie",
    "app_code",
    "appcode",
    "api_key",
    "apikey",
    "tracking_number",
    "waybill_code",
    "mail_no",
    "raw_payload",
    "provider_payload",
)


def sanitize_tracking_metadata(value: Any, *, depth: int = 0) -> Any:
    if depth > 6:
        return "[TRUNCATED]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:256]
    if isinstance(value, (list, tuple, set)):
        return [sanitize_tracking_metadata(item, depth=depth + 1) for item in list(value)[:20]]
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in list(value.items())[:40]:
            normalized = str(key).strip().lower()
            if normalized not in _SAFE_IDENTIFIER_KEYS and any(part in normalized for part in _SENSITIVE_KEY_PARTS):
                continue
            safe[str(key)[:80]] = sanitize_tracking_metadata(item, depth=depth + 1)
        return safe
    return type(value).__name__


def evidence_state_for(result: TrackingFactResult) -> str:
    if result.fact_evidence_present:
        return EVIDENCE_AVAILABLE
    value = " ".join(
        part for part in (result.failure_reason, result.tool_status) if part
    ).lower()
    if "timeout" in value or "timed_out" in value:
        return EVIDENCE_TIMEOUT
    if any(token in value for token in ("no_events", "no_evidence", "not_found", "no_match", "empty")):
        return EVIDENCE_NO_EVIDENCE
    if any(token in value for token in ("unavailable", "connection", "network", "disabled", "unsupported", "error")):
        return EVIDENCE_UNAVAILABLE
    return EVIDENCE_NO_EVIDENCE


def safe_used_source(
    *,
    source: str,
    tool_name: str,
    authority: str,
    evidence_state: str,
    observed_at: str | None = None,
    freshness: str = "unknown",
) -> dict[str, Any]:
    return {
        "source": str(source or "unknown")[:120],
        "tool_name": str(tool_name or "unknown")[:120],
        "authority": authority if authority in AUTHORITIES else AUTHORITY_NONE,
        "evidence_state": evidence_state if evidence_state in EVIDENCE_STATES else EVIDENCE_UNAVAILABLE,
        "observed_at": str(observed_at)[:64] if observed_at else None,
        "freshness": freshness if freshness in {"fresh", "stale", "unknown"} else "unknown",
    }


@dataclass(frozen=True)
class TrackingTruthResult(TrackingFactResult):
    """Structured truth-layer result. It is provider context, never an outbound message."""

    observed_at: str | None = None
    freshness: str = "unknown"
    evidence_state: str = EVIDENCE_NO_EVIDENCE
    source_authority: str = AUTHORITY_PRIMARY
    used_sources: list[dict[str, Any]] = field(default_factory=list)
    contradictions: list[dict[str, Any]] = field(default_factory=list)

    def metadata_payload(self) -> dict[str, Any]:
        payload = super().metadata_payload()
        payload.update(
            {
                "observed_at": self.observed_at,
                "freshness": self.freshness,
                "evidence_state": self.evidence_state,
                "source_authority": self.source_authority,
                "used_sources": self.used_sources,
                "contradictions": self.contradictions,
            }
        )
        return sanitize_tracking_metadata(payload)


def as_truth_result(
    result: TrackingFactResult,
    *,
    authority: str = AUTHORITY_PRIMARY,
    evidence_state: str | None = None,
    observed_at: str | None = None,
    freshness: str | None = None,
    used_sources: list[dict[str, Any]] | None = None,
    contradictions: list[dict[str, Any]] | None = None,
    **overrides: Any,
) -> TrackingTruthResult:
    base_fields = {item.name for item in fields(TrackingFactResult)}
    values = {name: getattr(result, name) for name in base_fields}
    for derived_name in ("observed_at", "freshness", "evidence_state", "source_authority", "used_sources", "contradictions"):
        values.pop(derived_name, None)
    values.update(overrides)
    state = evidence_state or evidence_state_for(result)
    resolved_observed_at = observed_at or getattr(result, "checked_at", None)
    resolved_freshness = freshness or ("fresh" if result.fact_evidence_present else "unknown")
    sources = used_sources or [
        safe_used_source(
            source=str(values.get("source") or "unknown"),
            tool_name=str(values.get("tool_name") or "unknown"),
            authority=authority,
            evidence_state=state,
            observed_at=resolved_observed_at,
            freshness=resolved_freshness,
        )
    ]
    return TrackingTruthResult(
        **values,
        observed_at=resolved_observed_at,
        freshness=resolved_freshness,
        evidence_state=state,
        source_authority=authority if authority in AUTHORITIES else AUTHORITY_NONE,
        used_sources=[sanitize_tracking_metadata(item) for item in sources[:10]],
        contradictions=[
            sanitize_tracking_metadata(item)
            for item in (contradictions or [])[:10]
        ],
    )
