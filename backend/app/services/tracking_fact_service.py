from __future__ import annotations

import re
import time
from typing import Any, Iterable

from ..settings import get_settings
from .speedaf.tracking_fact_source import (
    lookup_speedaf_hybrid_tracking_fact,
    lookup_speedaf_track_history_fact,
    lookup_speedaf_tracking_fact,
)
from .tracking_fact_schema import TrackingFactResult
from .tool_governance import record_tool_call

settings = get_settings()

TRACKING_NUMBER_RE = re.compile(r"(?<![A-Z0-9])([A-Z0-9][A-Z0-9-]{7,34}[A-Z0-9])(?![A-Z0-9])", re.IGNORECASE)
TRACKING_CONTEXT_RE = re.compile(
    r"\b(track|tracking|parcel|package|shipment|waybill|delivery|order)\b|查件|查询|物流|包裹|快递|单号|运单|订单号|订单",
    re.IGNORECASE,
)


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


def _tracking_tool_identity(source: str) -> tuple[str, str]:
    if source in {"speedaf_api", "speedaf_hybrid"}:
        return "speedaf.order.query", "speedaf_mcp"
    if source == "speedaf_track_query":
        return "speedaf.express.track.query", "speedaf_track_query"
    return "speedaf.order.query", "unsupported_tracking_source"


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
    record_tool_call(
        tool_name=tool_name,
        provider=provider,
        tool_type="read_only",
        input_payload={
            "tracking_number_hash_source": tracking_number,
            "conversation_id": conversation_id,
            "ticket_id": ticket_id,
            "request_id": request_id,
            "source": source,
        },
        output_payload=output_payload,
        status=status,
        error_code=error_code,
        error_message=error_message,
        elapsed_ms=elapsed_ms,
        timeout_ms=timeout_ms,
        conversation_id=str(conversation_id) if conversation_id is not None else None,
        webchat_conversation_id=safe_webchat_conversation_id,
        ticket_id=safe_ticket_id,
        request_id=request_id,
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
        result = TrackingFactResult(ok=False, tool_status="skipped", pii_redacted=True, failure_reason="missing_tracking_number")
        _audit_tracking_lookup(
            tracking_number=tracking_number,
            conversation_id=conversation_id,
            ticket_id=ticket_id,
            request_id=request_id,
            status="skipped",
            output_payload={"tool_status": result.tool_status, "failure_reason": result.failure_reason},
            elapsed_ms=int((time.monotonic() - started) * 1000),
            timeout_ms=timeout_ms,
        )
        return result
    if not getattr(settings, "webchat_tracking_fact_lookup_enabled", False):
        result = TrackingFactResult(ok=False, tracking_number=tracking_number, tool_status="disabled", pii_redacted=True, failure_reason="tracking_fact_lookup_disabled")
        _audit_tracking_lookup(
            tracking_number=tracking_number,
            conversation_id=conversation_id,
            ticket_id=ticket_id,
            request_id=request_id,
            status="skipped",
            output_payload={"tool_status": result.tool_status, "failure_reason": result.failure_reason},
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
        return lookup_speedaf_tracking_fact(**kwargs)
    if source == "speedaf_track_query":
        return lookup_speedaf_track_history_fact(
            tracking_number=tracking_number,
            conversation_id=conversation_id,
            ticket_id=ticket_id,
            request_id=request_id,
        )
    if source == "speedaf_hybrid":
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
    result = TrackingFactResult(ok=False, tracking_number=tracking_number, tool_status="unsupported_source", pii_redacted=True, failure_reason="unsupported_tracking_fact_source")
    _audit_tracking_lookup(
        tracking_number=tracking_number,
        conversation_id=conversation_id,
        ticket_id=ticket_id,
        request_id=request_id,
        status="skipped",
        output_payload={"tool_status": result.tool_status, "failure_reason": result.failure_reason},
        elapsed_ms=int((time.monotonic() - started) * 1000),
        timeout_ms=timeout_ms,
    )
    return result
