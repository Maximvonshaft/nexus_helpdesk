from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
from typing import Any, Iterable

from ..settings import get_settings
from ..utils.time import utc_now
from .tracking_fact_redactor import normalize_tracking_fact
from .tracking_fact_schema import TrackingFactResult
from .tool_governance import record_tool_call

LOGGER = logging.getLogger("nexusdesk")
settings = get_settings()

TRACKING_NUMBER_RE = re.compile(r"\b([A-Z0-9][A-Z0-9-]{7,34}[A-Z0-9])\b", re.IGNORECASE)


def extract_tracking_number(*values: str | None) -> str | None:
    for value in values:
        text = (value or "").strip()
        if not text:
            continue
        for match in TRACKING_NUMBER_RE.finditer(text):
            candidate = match.group(1).strip().upper().replace("-", "")
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
    record_tool_call(
        tool_name="openclaw_bridge.speedaf_lookup",
        provider="openclaw_bridge",
        tool_type="read_only",
        input_payload={
            "tracking_number_hash_source": tracking_number,
            "conversation_id": conversation_id,
            "ticket_id": ticket_id,
            "request_id": request_id,
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
) -> TrackingFactResult:
    tracking_number = (tracking_number or "").strip().upper()
    started = time.monotonic()
    timeout_seconds = max(1, min(int(getattr(settings, "webchat_tracking_fact_timeout_seconds", 8) or 8), 30))
    timeout_ms = timeout_seconds * 1000
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
    if getattr(settings, "webchat_tracking_fact_source", "openclaw_bridge") != "openclaw_bridge":
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

    bridge_url = settings.openclaw_bridge_url.rstrip("/")
    payload = {
        "tracking_number": tracking_number,
        "source": "nexus_webchat",
        "request_id": request_id,
        "conversation_id": conversation_id,
        "ticket_id": ticket_id,
    }
    request = urllib.request.Request(
        f"{bridge_url}/tools/speedaf_lookup",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            parsed = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        elapsed_ms = int((time.monotonic() - started) * 1000)
        LOGGER.warning(
            "webchat_tracking_fact_lookup_http_failed",
            extra={"event_payload": {
                "ticket_id": ticket_id,
                "conversation_id": conversation_id,
                "status_code": exc.code,
                "error_preview": body[:200],
            }},
        )
        _audit_tracking_lookup(
            tracking_number=tracking_number,
            conversation_id=conversation_id,
            ticket_id=ticket_id,
            request_id=request_id,
            status="failed",
            output_payload={"status_code": exc.code},
            error_code=f"bridge_http_{exc.code}",
            error_message=body[:300],
            elapsed_ms=elapsed_ms,
            timeout_ms=timeout_ms,
        )
        return TrackingFactResult(ok=False, tracking_number=tracking_number, tool_status="http_error", pii_redacted=True, failure_reason=f"bridge_http_{exc.code}")
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        reason = "timeout" if "timed out" in str(exc).lower() or isinstance(exc, TimeoutError) else "bridge_error"
        elapsed_ms = int((time.monotonic() - started) * 1000)
        LOGGER.warning(
            "webchat_tracking_fact_lookup_failed",
            extra={"event_payload": {
                "ticket_id": ticket_id,
                "conversation_id": conversation_id,
                "error_type": type(exc).__name__,
                "failure_reason": reason,
            }},
        )
        _audit_tracking_lookup(
            tracking_number=tracking_number,
            conversation_id=conversation_id,
            ticket_id=ticket_id,
            request_id=request_id,
            status="timeout" if reason == "timeout" else "failed",
            output_payload={"failure_reason": reason},
            error_code=type(exc).__name__,
            error_message=str(exc),
            elapsed_ms=elapsed_ms,
            timeout_ms=timeout_ms,
        )
        return TrackingFactResult(ok=False, tracking_number=tracking_number, tool_status="error", pii_redacted=True, failure_reason=reason)

    elapsed_ms = int((time.monotonic() - started) * 1000)
    if not isinstance(parsed, dict):
        _audit_tracking_lookup(
            tracking_number=tracking_number,
            conversation_id=conversation_id,
            ticket_id=ticket_id,
            request_id=request_id,
            status="failed",
            output_payload={"failure_reason": "invalid_bridge_response"},
            error_code="invalid_bridge_response",
            elapsed_ms=elapsed_ms,
            timeout_ms=timeout_ms,
        )
        return TrackingFactResult(ok=False, tracking_number=tracking_number, tool_status="invalid", pii_redacted=True, failure_reason="invalid_bridge_response")
    if not parsed.get("ok", False):
        normalized = normalize_tracking_fact(parsed, tracking_number=tracking_number)
        failure_reason = normalized.failure_reason or str(parsed.get("error") or "tool_lookup_failed")
        _audit_tracking_lookup(
            tracking_number=tracking_number,
            conversation_id=conversation_id,
            ticket_id=ticket_id,
            request_id=request_id,
            status="failed",
            output_payload={"tool_status": normalized.tool_status, "failure_reason": failure_reason},
            error_code=str(parsed.get("error_code") or parsed.get("tool_status") or "tool_lookup_failed"),
            error_message=failure_reason,
            elapsed_ms=elapsed_ms,
            timeout_ms=timeout_ms,
        )
        if normalized.failure_reason:
            return normalized
        return TrackingFactResult(ok=False, tracking_number=tracking_number, tool_status=str(parsed.get("tool_status") or "error"), pii_redacted=True, failure_reason=failure_reason)

    raw_result: dict[str, Any]
    result_value = parsed.get("result") or parsed.get("data") or parsed
    raw_result = result_value if isinstance(result_value, dict) else {"result": result_value}
    if "checked_at" not in raw_result:
        raw_result["checked_at"] = utc_now().isoformat()
    if "tracking_number" not in raw_result:
        raw_result["tracking_number"] = tracking_number
    if "tool_status" not in raw_result:
        raw_result["tool_status"] = parsed.get("tool_status") or "success"
    normalized = normalize_tracking_fact(raw_result, tracking_number=tracking_number)
    _audit_tracking_lookup(
        tracking_number=tracking_number,
        conversation_id=conversation_id,
        ticket_id=ticket_id,
        request_id=request_id,
        status="success" if normalized.ok else "failed",
        output_payload=normalized.metadata_payload(),
        error_code=None if normalized.ok else normalized.tool_status,
        error_message=normalized.failure_reason,
        elapsed_ms=elapsed_ms,
        timeout_ms=timeout_ms,
    )
    return normalized
