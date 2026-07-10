from __future__ import annotations

import os
import re
import time
from typing import Any, Iterable

from ..settings import get_settings
from .speedaf.tracking_fact_source import (
    lookup_speedaf_hybrid_tracking_fact,
    lookup_speedaf_track_history_fact,
    lookup_speedaf_tracking_fact,
)
from .tracking_fact_schema import (
    EVIDENCE_NO_EVIDENCE,
    EVIDENCE_UNAVAILABLE,
    FRESHNESS_UNKNOWN,
    SOURCE_AUTHORITY_ENRICHMENT,
    SOURCE_AUTHORITY_NONE,
    SOURCE_AUTHORITY_PRIMARY,
    TrackingFactResult,
    as_tracking_truth_result,
    safe_tracking_candidate,
    safe_used_source,
    sanitize_tracking_metadata,
)
from .tool_governance import record_tool_call

settings = get_settings()

TRACKING_NUMBER_RE = re.compile(r"(?<![A-Z0-9])([A-Z0-9][A-Z0-9-]{7,34}[A-Z0-9])(?![A-Z0-9])", re.IGNORECASE)
TRACKING_CONTEXT_RE = re.compile(
    r"\b(track|tracking|parcel|package|shipment|waybill|delivery|order)\b|查件|查询|物流|包裹|快递|单号|运单|订单号|订单",
    re.IGNORECASE,
)
TRACKING_FACT_SOURCE_ALLOWLIST = {"speedaf_api", "speedaf_track_query", "speedaf_hybrid"}


def extract_tracking_number(*values: str | None) -> str | None:
    for value in values:
        text = (value or "").strip()
        if not text:
            continue
        for match in TRACKING_NUMBER_RE.finditer(text):
            candidate = re.sub(r"[-\u2010-\u2015\u2212]+", "", match.group(1).strip().upper())
            if candidate.isdigit() and not TRACKING_CONTEXT_RE.search(text):
                continue
            if _looks_like_tracking_number(candidate):
                return candidate
    return None


def extract_tracking_number_from_history(values: Iterable[str | None]) -> str | None:
    return extract_tracking_number(*list(values))


def _looks_like_tracking_number(candidate: str) -> bool:
    if not 8 <= len(candidate) <= 35:
        return False
    if candidate.isalpha():
        return False
    if candidate.isdigit() and len(candidate) < 10:
        return False
    return bool(re.search(r"\d", candidate))


def _current_tracking_fact_source() -> str:
    return (getattr(settings, "webchat_tracking_fact_source", "speedaf_api") or "speedaf_api").strip().lower()


def _hybrid_gate_enabled() -> bool:
    configured = getattr(settings, "webchat_tracking_hybrid_enabled", None)
    if configured is not None:
        return bool(configured)
    return os.getenv("WEBCHAT_TRACKING_HYBRID_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def _tracking_tool_identity(source: str) -> tuple[str, str]:
    if source in {"speedaf_api", "speedaf_hybrid"}:
        return "speedaf.order.query", "speedaf_mcp"
    if source == "speedaf_track_query":
        return "speedaf.express.track.query", "speedaf_track_query"
    return "speedaf.order.query", "unsupported_tracking_source"


def _safe_audit_output(output_payload: Any) -> dict[str, Any]:
    if isinstance(output_payload, TrackingFactResult):
        return output_payload.metadata_payload()
    if isinstance(output_payload, dict):
        sanitized = sanitize_tracking_metadata(output_payload)
        return sanitized if isinstance(sanitized, dict) else {"result_type": "dict"}
    return {"result_type": type(output_payload).__name__} if output_payload is not None else {}


def _audit_tracking_lookup(
    *,
    tracking_number: str | None,
    conversation_id: int | str | None,
    ticket_id: int | str | None,
    request_id: str | None,
    status: str,
    output_payload: Any = None,
    error_code: str | None = None,
    error_message: str | None = None,
    elapsed_ms: int | None = None,
    timeout_ms: int | None = None,
) -> None:
    safe_ticket_id = int(ticket_id) if isinstance(ticket_id, int) or (isinstance(ticket_id, str) and ticket_id.isdigit()) else None
    safe_webchat_conversation_id = int(conversation_id) if isinstance(conversation_id, int) or (isinstance(conversation_id, str) and conversation_id.isdigit()) else None
    source = _current_tracking_fact_source()
    tool_name, provider = _tracking_tool_identity(source)
    safe_input: dict[str, Any] = safe_tracking_candidate(tracking_number)
    safe_input.update(
        {
            "conversation_id": safe_webchat_conversation_id,
            "ticket_id": safe_ticket_id,
            "request_id": str(request_id)[:120] if request_id else None,
            "source": source[:80],
        }
    )
    record_tool_call(
        tool_name=tool_name,
        provider=provider,
        tool_type="read_only",
        input_payload={key: value for key, value in safe_input.items() if value is not None},
        output_payload=_safe_audit_output(output_payload),
        status=status,
        error_code=str(error_code)[:120] if error_code else None,
        error_message=(str(error_code or "tracking_lookup_failed")[:120] if error_message else None),
        elapsed_ms=elapsed_ms,
        timeout_ms=timeout_ms,
        conversation_id=str(safe_webchat_conversation_id) if safe_webchat_conversation_id is not None else None,
        webchat_conversation_id=safe_webchat_conversation_id,
        ticket_id=safe_ticket_id,
        request_id=str(request_id)[:120] if request_id else None,
    )


def _history_enrichment_only(result: TrackingFactResult) -> TrackingFactResult:
    observed_at = result.observed_at or result.checked_at
    source_state = result.evidence_state
    if result.fact_evidence_present:
        source_state = "available"
    return as_tracking_truth_result(
        result,
        authority=SOURCE_AUTHORITY_NONE,
        evidence_state=EVIDENCE_NO_EVIDENCE,
        observed_at=observed_at,
        freshness=FRESHNESS_UNKNOWN,
        used_sources=[
            safe_used_source(
                source=result.source,
                tool_name=result.tool_name,
                authority=SOURCE_AUTHORITY_ENRICHMENT,
                evidence_state=source_state,
                observed_at=observed_at,
                freshness=result.freshness or FRESHNESS_UNKNOWN,
            )
        ],
        status=None,
        status_label=None,
        latest_event=None,
        fact_evidence_present=False,
        failure_reason=result.failure_reason or "history_enrichment_is_not_current_status",
        failure_summary="History is enrichment only; no primary current-status fact is available.",
    )


def lookup_tracking_fact(
    *,
    tracking_number: str | None,
    conversation_id: int | str | None = None,
    ticket_id: int | str | None = None,
    request_id: str | None = None,
    caller_id: str | None = None,
    country_code: str | None = None,
) -> TrackingFactResult:
    tracking_number = (tracking_number or "").strip().upper()
    started = time.monotonic()
    timeout_seconds = max(1, min(int(getattr(settings, "webchat_tracking_fact_timeout_seconds", 8) or 8), 30))
    timeout_ms = timeout_seconds * 1000
    source = _current_tracking_fact_source()

    if not tracking_number:
        result = as_tracking_truth_result(
            TrackingFactResult(ok=False, tool_status="skipped", pii_redacted=True, failure_reason="missing_tracking_number"),
            authority=SOURCE_AUTHORITY_NONE,
        )
        _audit_tracking_lookup(
            tracking_number=tracking_number,
            conversation_id=conversation_id,
            ticket_id=ticket_id,
            request_id=request_id,
            status="skipped",
            output_payload=result,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            timeout_ms=timeout_ms,
        )
        return result

    if not getattr(settings, "webchat_tracking_fact_lookup_enabled", False):
        result = as_tracking_truth_result(
            TrackingFactResult(
                ok=False,
                tracking_number=tracking_number,
                tool_status="disabled",
                pii_redacted=True,
                failure_reason="tracking_fact_lookup_disabled",
            ),
            authority=SOURCE_AUTHORITY_NONE,
            evidence_state=EVIDENCE_UNAVAILABLE,
        )
        _audit_tracking_lookup(
            tracking_number=tracking_number,
            conversation_id=conversation_id,
            ticket_id=ticket_id,
            request_id=request_id,
            status="skipped",
            output_payload=result,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            timeout_ms=timeout_ms,
        )
        return result

    if source not in TRACKING_FACT_SOURCE_ALLOWLIST:
        result = as_tracking_truth_result(
            TrackingFactResult(
                ok=False,
                tracking_number=tracking_number,
                tool_status="unsupported_source",
                pii_redacted=True,
                failure_reason="unsupported_tracking_fact_source",
            ),
            authority=SOURCE_AUTHORITY_NONE,
            evidence_state=EVIDENCE_UNAVAILABLE,
        )
        _audit_tracking_lookup(
            tracking_number=tracking_number,
            conversation_id=conversation_id,
            ticket_id=ticket_id,
            request_id=request_id,
            status="skipped",
            output_payload=result,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            timeout_ms=timeout_ms,
        )
        return result

    if source == "speedaf_api":
        kwargs = {
            "tracking_number": tracking_number,
            "caller_id": caller_id,
            "conversation_id": conversation_id,
            "ticket_id": ticket_id,
            "request_id": request_id,
        }
        if country_code is not None:
            kwargs["country_code"] = country_code
        return as_tracking_truth_result(lookup_speedaf_tracking_fact(**kwargs), authority=SOURCE_AUTHORITY_PRIMARY)

    if source == "speedaf_track_query":
        history = lookup_speedaf_track_history_fact(
            tracking_number=tracking_number,
            conversation_id=conversation_id,
            ticket_id=ticket_id,
            request_id=request_id,
        )
        return _history_enrichment_only(history)

    if not _hybrid_gate_enabled():
        result = as_tracking_truth_result(
            TrackingFactResult(
                ok=False,
                tracking_number=tracking_number,
                source="speedaf_api.hybrid_order_query_plus_track_query",
                tool_name="speedaf.order.query",
                tool_status="disabled",
                pii_redacted=True,
                fact_evidence_present=False,
                failure_reason="speedaf_hybrid_gate_disabled",
            ),
            authority=SOURCE_AUTHORITY_NONE,
            evidence_state=EVIDENCE_UNAVAILABLE,
        )
        _audit_tracking_lookup(
            tracking_number=tracking_number,
            conversation_id=conversation_id,
            ticket_id=ticket_id,
            request_id=request_id,
            status="skipped",
            output_payload=result,
            error_code=result.failure_reason,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            timeout_ms=timeout_ms,
        )
        return result

    kwargs = {
        "tracking_number": tracking_number,
        "caller_id": caller_id,
        "conversation_id": conversation_id,
        "ticket_id": ticket_id,
        "request_id": request_id,
    }
    if country_code is not None:
        kwargs["country_code"] = country_code
    return lookup_speedaf_hybrid_tracking_fact(**kwargs)
