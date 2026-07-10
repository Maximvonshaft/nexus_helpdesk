from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from typing import Any

TRACKING_FACT_SOURCE = "speedaf_api.tracking_lookup"
TRACKING_FACT_TOOL_NAME = "speedaf.order.query"

EVIDENCE_AVAILABLE = "available"
EVIDENCE_STALE = "stale"
EVIDENCE_TIMEOUT = "timeout"
EVIDENCE_UNAVAILABLE = "unavailable"
EVIDENCE_CONTRADICTORY = "contradictory"
EVIDENCE_NO_EVIDENCE = "no_evidence"
EVIDENCE_STATES = {
    EVIDENCE_AVAILABLE,
    EVIDENCE_STALE,
    EVIDENCE_TIMEOUT,
    EVIDENCE_UNAVAILABLE,
    EVIDENCE_CONTRADICTORY,
    EVIDENCE_NO_EVIDENCE,
}

SOURCE_AUTHORITY_PRIMARY = "primary_current_status"
SOURCE_AUTHORITY_ENRICHMENT = "history_enrichment"
SOURCE_AUTHORITY_NONE = "none"
SOURCE_AUTHORITIES = {
    SOURCE_AUTHORITY_PRIMARY,
    SOURCE_AUTHORITY_ENRICHMENT,
    SOURCE_AUTHORITY_NONE,
}

FRESHNESS_FRESH = "fresh"
FRESHNESS_STALE = "stale"
FRESHNESS_UNKNOWN = "unknown"
FRESHNESS_STATES = {FRESHNESS_FRESH, FRESHNESS_STALE, FRESHNESS_UNKNOWN}

_SAFE_IDENTIFIER_KEYS = {
    "tracking_number_hash",
    "tracking_reference_suffix",
    "safe_tracking_reference",
    "waybill_hash",
    "waybill_suffix",
}
_TRACKING_LIKE_RE = re.compile(r"(?<![A-Z0-9])[A-Z0-9][A-Z0-9-]{7,34}[A-Z0-9](?![A-Z0-9])", re.IGNORECASE)

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
    "caller_id",
    "phone",
    "email",
    "address",
    "recipient",
    "raw_payload",
    "provider_payload",
    "raw_output",
    "raw_error",
)


def hash_tracking_number(tracking_number: str | None) -> str | None:
    value = (tracking_number or "").strip().upper()
    if not value:
        return None
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def safe_tracking_candidate(waybill_code: str | None, suffix: str | None = None) -> dict[str, str]:
    cleaned = (waybill_code or "").strip().upper()
    safe_suffix = (suffix or cleaned[-4:]).strip()[-4:] if (suffix or cleaned) else ""
    payload: dict[str, str] = {}
    if safe_suffix:
        payload["waybill_suffix"] = safe_suffix
    hashed = hash_tracking_number(cleaned)
    if hashed:
        payload["waybill_hash"] = hashed
    return payload


def safe_tracking_reference(tracking_number: str | None) -> str:
    cleaned = re.sub(r"[^A-Z0-9]", "", (tracking_number or "").strip().upper())
    if len(cleaned) >= 6:
        return f"parcel ending {cleaned[-6:]}"
    if len(cleaned) >= 4:
        return f"parcel ending {cleaned[-4:]}"
    return "the parcel reference provided by the customer"


def _redact_tracking_identifier(match: re.Match[str]) -> str:
    token = match.group(0)
    return "[REDACTED_IDENTIFIER]" if any(character.isdigit() for character in token) else token


def sanitize_tracking_metadata(value: Any, *, depth: int = 0) -> Any:
    """Return bounded audit-safe metadata without raw identifiers or provider payloads."""

    if depth > 6:
        return "[TRUNCATED]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        bounded = value[:256]
        return _TRACKING_LIKE_RE.sub(_redact_tracking_identifier, bounded)
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


def evidence_state_for(*, fact_evidence_present: bool, failure_reason: str | None, tool_status: str | None) -> str:
    if fact_evidence_present:
        return EVIDENCE_AVAILABLE
    value = " ".join(part for part in (failure_reason, tool_status) if part).lower()
    if "timeout" in value or "timed_out" in value:
        return EVIDENCE_TIMEOUT
    if any(token in value for token in ("not_found", "no_match", "no_events", "no_evidence", "empty")):
        return EVIDENCE_NO_EVIDENCE
    if any(token in value for token in ("unavailable", "connection", "network", "disabled", "unsupported", "error")):
        return EVIDENCE_UNAVAILABLE
    return EVIDENCE_NO_EVIDENCE


def safe_used_source(
    *,
    source: str | None,
    tool_name: str | None,
    authority: str,
    evidence_state: str,
    observed_at: str | None,
    freshness: str,
) -> dict[str, Any]:
    return {
        "source": str(source or "unknown")[:120],
        "tool_name": str(tool_name or "unknown")[:120],
        "authority": authority if authority in SOURCE_AUTHORITIES else SOURCE_AUTHORITY_NONE,
        "evidence_state": evidence_state if evidence_state in EVIDENCE_STATES else EVIDENCE_UNAVAILABLE,
        "observed_at": str(observed_at)[:64] if observed_at else None,
        "freshness": freshness if freshness in FRESHNESS_STATES else FRESHNESS_UNKNOWN,
    }


@dataclass(frozen=True)
class TrackingFactEvent:
    event_time: str | None = None
    location: str | None = None
    description: str | None = None

    def to_safe_dict(self) -> dict[str, str | None]:
        return {
            "event_time": self.event_time,
            "location": self.location,
            "description": self.description,
        }

    def is_present(self) -> bool:
        return bool((self.event_time or "").strip() or (self.location or "").strip() or (self.description or "").strip())


@dataclass(frozen=True)
class TrackingFactResult:
    ok: bool
    tracking_number: str | None = None
    status: str | None = None
    status_label: str | None = None
    latest_event: TrackingFactEvent | None = None
    events_summary: list[TrackingFactEvent] = field(default_factory=list)
    checked_at: str | None = None
    source: str = TRACKING_FACT_SOURCE
    tool_name: str = TRACKING_FACT_TOOL_NAME
    tool_status: str | None = None
    pii_redacted: bool = False
    fact_evidence_present: bool = False
    failure_reason: str | None = None
    failure_summary: str | None = None
    failure_retryable: bool | None = None
    failure_needs_customer_confirmation: bool | None = None
    failure_needs_human_review: bool | None = None
    safe_candidates: list[dict[str, Any]] = field(default_factory=list)
    lifecycle_summary: dict[str, Any] = field(default_factory=dict)
    status_context: dict[str, Any] = field(default_factory=dict)
    lookup_elapsed_ms: int | None = None
    used_sources: list[dict[str, Any]] = field(default_factory=list)
    source_authority: str = SOURCE_AUTHORITY_NONE
    observed_at: str | None = None
    freshness: str = FRESHNESS_UNKNOWN
    evidence_state: str = EVIDENCE_NO_EVIDENCE
    contradictions: list[dict[str, Any]] = field(default_factory=list)

    def metadata_payload(self) -> dict[str, Any]:
        cleaned_tracking = re.sub(r"[^A-Z0-9]", "", (self.tracking_number or "").strip().upper())
        payload: dict[str, Any] = {
            "fact_evidence_present": self.fact_evidence_present,
            "fact_source": self.source,
            "tool_name": self.tool_name,
            "tool_status": self.tool_status,
            "pii_redacted": self.pii_redacted,
            "checked_at": self.checked_at,
            "observed_at": self.observed_at,
            "freshness": self.freshness,
            "evidence_state": self.evidence_state,
            "source_authority": self.source_authority,
            "used_sources": self.used_sources[:10],
            "contradictions": self.contradictions[:10],
            "tracking_number_hash": hash_tracking_number(self.tracking_number),
            "tracking_reference_suffix": cleaned_tracking[-6:] if len(cleaned_tracking) >= 6 else (cleaned_tracking[-4:] if len(cleaned_tracking) >= 4 else None),
            "safe_tracking_reference": safe_tracking_reference(self.tracking_number) if cleaned_tracking else None,
        }
        if self.lookup_elapsed_ms is not None:
            payload["lookup_elapsed_ms"] = self.lookup_elapsed_ms
        if self.safe_candidates:
            payload["safe_candidates"] = self.safe_candidates[:10]
            payload["candidate_count"] = len(self.safe_candidates)
        if self.lifecycle_summary:
            payload["tracking_lifecycle"] = self.lifecycle_summary
        if self.status_context:
            payload["status_context"] = self.status_context
        if self.failure_reason:
            payload["tracking_fact_failure_reason"] = self.failure_reason[:120]
        if self.failure_summary:
            payload["tracking_fact_failure_summary"] = self.failure_summary[:256]
        if self.failure_retryable is not None:
            payload["tracking_fact_failure_retryable"] = self.failure_retryable
        if self.failure_needs_customer_confirmation is not None:
            payload["tracking_fact_failure_needs_customer_confirmation"] = self.failure_needs_customer_confirmation
        if self.failure_needs_human_review is not None:
            payload["tracking_fact_failure_needs_human_review"] = self.failure_needs_human_review
        return sanitize_tracking_metadata({key: value for key, value in payload.items() if value is not None})

    def prompt_summary(self) -> str:
        if self.failure_reason == "multiple_waybill_candidates" and self.safe_candidates:
            suffixes = ", ".join(str(item.get("waybill_suffix")) for item in self.safe_candidates if item.get("waybill_suffix"))
            return (
                "Trusted tracking lookup result:\n"
                "- Multiple shipments are linked to this caller.\n"
                f"- Safe candidate suffixes: {suffixes or 'available'}\n"
                "Rules:\n"
                "Ask the customer to confirm the last four digits before giving a parcel status.\n"
                "Do not reveal or infer the full waybill number."
            )
        if not self.fact_evidence_present:
            if self.failure_reason:
                lines = [
                    "Trusted tracking lookup result:",
                    f"- Source: {self.source}",
                    f"- Checked at: {self.checked_at or 'unknown'}",
                    f"- Tracking reference: {safe_tracking_reference(self.tracking_number)}",
                    f"- Result: {self.failure_summary or self.failure_reason}",
                    "Rules:",
                    "Do not claim a live parcel status because no trusted tracking fact is available.",
                    "Ask only for the minimum missing or corrected customer information needed to continue.",
                    "Do not mention internal tools, provider names, raw error codes, or raw backend output.",
                    "Do not reveal or repeat the full tracking number.",
                ]
                return "\n".join(lines)
            return ""
        lines = [
            "Trusted tracking fact:",
            f"- Source: {self.source}",
            f"- Checked at: {self.checked_at or 'unknown'}",
            f"- Tracking reference: {safe_tracking_reference(self.tracking_number)}",
            f"- Current status: {self.status_label or self.status or 'unknown'}",
            f"- PII redacted: {str(self.pii_redacted).lower()}",
        ]
        status_code = self.status_context.get("code") if self.status_context else self.status
        if status_code:
            lines.append(f"- Speedaf status code: {status_code}")
        if self.status_context:
            meaning_parts = [self.status_context.get("label"), self.status_context.get("description")]
            compact_meaning = " - ".join(str(part) for part in meaning_parts if part)
            if compact_meaning:
                lines.append(f"- Status meaning: {compact_meaning}")
            handling_hint = self.status_context.get("handling_hint")
            if handling_hint:
                lines.append(f"- Status handling hint: {handling_hint}")
            if self.status_context.get("needs_human_review") is True:
                lines.append("- Status risk: human review may be required.")
        if self.latest_event and self.latest_event.is_present():
            event = self.latest_event
            latest_parts = [part for part in [event.description, event.location, event.event_time] if part]
            if latest_parts:
                lines.append(f"- Latest event: {' | '.join(latest_parts)}")
        safe_events = [event for event in self.events_summary if event.is_present()][:3]
        if safe_events:
            lines.append("- Recent events:")
            for event in safe_events:
                parts = [part for part in [event.description, event.location, event.event_time] if part]
                if parts:
                    lines.append(f"  - {' | '.join(parts)}")
        if self.lifecycle_summary:
            latest_milestone = self.lifecycle_summary.get("latest_milestone")
            latest_action = self.lifecycle_summary.get("latest_action")
            risk = self.lifecycle_summary.get("risk") if isinstance(self.lifecycle_summary.get("risk"), dict) else {}
            durations = self.lifecycle_summary.get("durations") if isinstance(self.lifecycle_summary.get("durations"), dict) else {}
            lifecycle_parts = [part for part in [latest_milestone, f"action {latest_action}" if latest_action else None] if part]
            if lifecycle_parts:
                lines.append(f"- Lifecycle: {' | '.join(str(part) for part in lifecycle_parts)}")
            if risk.get("escalate_required") is True:
                lines.append("- Lifecycle risk: human review may be required.")
            if durations:
                compact = []
                for key in ("customs_hours", "last_mile_hours", "total_transit_hours"):
                    value = durations.get(key)
                    if isinstance(value, (int, float)):
                        compact.append(f"{key}={round(float(value), 1)}")
                if compact:
                    lines.append(f"- Lifecycle durations: {', '.join(compact)}")
        lines.extend([
            "Rules:",
            "Use only the trusted tracking fact above for parcel status.",
            "Do not ask the customer for the tracking number again when a tracking reference is present.",
            "Refer to the shipment by the safe tracking reference only.",
            "Do not mention internal tools, provider names, or raw tool output.",
            "Do not reveal or repeat the full tracking number.",
            "Do not reveal recipient names, POD signer names, phone numbers, emails, or detailed addresses.",
        ])
        return "\n".join(lines)


def as_tracking_truth_result(
    result: TrackingFactResult,
    *,
    authority: str = SOURCE_AUTHORITY_PRIMARY,
    evidence_state: str | None = None,
    observed_at: str | None = None,
    freshness: str | None = None,
    used_sources: list[dict[str, Any]] | None = None,
    contradictions: list[dict[str, Any]] | None = None,
    **overrides: Any,
) -> TrackingFactResult:
    state = evidence_state or evidence_state_for(
        fact_evidence_present=result.fact_evidence_present,
        failure_reason=result.failure_reason,
        tool_status=result.tool_status,
    )
    resolved_observed_at = observed_at or result.observed_at or result.checked_at
    if freshness is not None:
        resolved_freshness = freshness
    elif result.freshness in {FRESHNESS_FRESH, FRESHNESS_STALE}:
        resolved_freshness = result.freshness
    else:
        resolved_freshness = FRESHNESS_FRESH if result.fact_evidence_present else FRESHNESS_UNKNOWN
    resolved_authority = authority if authority in SOURCE_AUTHORITIES else SOURCE_AUTHORITY_NONE
    resolved_sources = used_sources or [
        safe_used_source(
            source=overrides.get("source", result.source),
            tool_name=overrides.get("tool_name", result.tool_name),
            authority=resolved_authority,
            evidence_state=state,
            observed_at=resolved_observed_at,
            freshness=resolved_freshness,
        )
    ]
    return replace(
        result,
        observed_at=resolved_observed_at,
        freshness=resolved_freshness if resolved_freshness in FRESHNESS_STATES else FRESHNESS_UNKNOWN,
        evidence_state=state if state in EVIDENCE_STATES else EVIDENCE_UNAVAILABLE,
        source_authority=resolved_authority,
        used_sources=[sanitize_tracking_metadata(item) for item in resolved_sources[:10]],
        contradictions=[sanitize_tracking_metadata(item) for item in (contradictions or result.contradictions)[:10]],
        **overrides,
    )
