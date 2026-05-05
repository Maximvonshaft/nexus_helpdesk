from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from typing import Any, Iterable

from ..settings import get_settings
from ..utils.time import utc_now
from .tracking_fact_redactor import normalize_tracking_fact
from .tracking_fact_schema import TrackingFactResult

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


def lookup_tracking_fact(
    *,
    tracking_number: str | None,
    conversation_id: int | str | None = None,
    ticket_id: int | str | None = None,
    request_id: str | None = None,
) -> TrackingFactResult:
    tracking_number = (tracking_number or "").strip().upper()
    if not tracking_number:
        return TrackingFactResult(ok=False, tool_status="skipped", pii_redacted=True, failure_reason="missing_tracking_number")
    if not getattr(settings, "webchat_tracking_fact_lookup_enabled", False):
        return TrackingFactResult(ok=False, tracking_number=tracking_number, tool_status="disabled", pii_redacted=True, failure_reason="tracking_fact_lookup_disabled")
    if getattr(settings, "webchat_tracking_fact_source", "openclaw_bridge") != "openclaw_bridge":
        return TrackingFactResult(ok=False, tracking_number=tracking_number, tool_status="unsupported_source", pii_redacted=True, failure_reason="unsupported_tracking_fact_source")

    bridge_url = settings.openclaw_bridge_url.rstrip("/")
    timeout_seconds = max(1, min(int(getattr(settings, "webchat_tracking_fact_timeout_seconds", 8) or 8), 30))
    payload = {
        "tracking_number": tracking_number,
        "source": "webchat_tracking_fact_probe",
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
        LOGGER.warning(
            "webchat_tracking_fact_lookup_http_failed",
            extra={"event_payload": {
                "ticket_id": ticket_id,
                "conversation_id": conversation_id,
                "status_code": exc.code,
                "error_preview": body[:200],
            }},
        )
        return TrackingFactResult(ok=False, tracking_number=tracking_number, tool_status="http_error", pii_redacted=True, failure_reason=f"bridge_http_{exc.code}")
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        reason = "timeout" if "timed out" in str(exc).lower() or isinstance(exc, TimeoutError) else "bridge_error"
        LOGGER.warning(
            "webchat_tracking_fact_lookup_failed",
            extra={"event_payload": {
                "ticket_id": ticket_id,
                "conversation_id": conversation_id,
                "error_type": type(exc).__name__,
                "failure_reason": reason,
            }},
        )
        return TrackingFactResult(ok=False, tracking_number=tracking_number, tool_status="error", pii_redacted=True, failure_reason=reason)

    if not isinstance(parsed, dict):
        return TrackingFactResult(ok=False, tracking_number=tracking_number, tool_status="invalid", pii_redacted=True, failure_reason="invalid_bridge_response")

    result_value: dict[str, Any]
    if "result" in parsed and isinstance(parsed.get("result"), dict):
        result_value = parsed["result"]
    elif "data" in parsed and isinstance(parsed.get("data"), dict):
        result_value = parsed["data"]
    else:
        result_value = parsed

    if "checked_at" not in result_value:
        result_value["checked_at"] = utc_now().isoformat()
    return normalize_tracking_fact(result_value, tracking_number=tracking_number)
