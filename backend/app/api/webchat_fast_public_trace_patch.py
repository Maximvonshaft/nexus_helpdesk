from __future__ import annotations

import os
import re
import time
from typing import Any

from . import webchat_fast as _wf

# Public WebChat responses are browser-visible. They must not expose raw
# waybill/tracking identifiers through debug traces, replay payloads, or
# benchmark-observed evidence paths. Internal provider audit rows may retain
# safe summaries, hashes, suffixes, and timings.
_CH_WAYBILL_FULL_RE = re.compile(r"^CH\d{12}$", re.IGNORECASE)
_CH_WAYBILL_CANDIDATE_RE = re.compile(r"^CH\d+$", re.IGNORECASE)
_CH_RAW_TOKEN_RE = re.compile(r"\bCH[\s_-]*\d(?:[\s_-]*\d){8,20}\b", re.IGNORECASE)
_LONG_NUMERIC_RE = re.compile(r"(?<!\d)\d{8,20}(?!\d)")
_REDACTION = "tracking_number_redacted"
_TRACE_KEYS = {
    "ai_decision_trace",
    "evidence_trace",
    "rag_trace",
    "runtime_context_trace",
    "query_analysis",
    "top_hits",
    "evidence_pack",
    "injected_knowledge",
    "grounding_source",
}
_NEGATIVE_CACHE: dict[str, tuple[float, str | None, str | None]] = {}
_NON_CACHEABLE_FAILURES = {"timeout", "network_error", "connection_error", "temporary_error"}


def _normalize_tracking_number(value: str | None) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()


def _is_invalid_ch_waybill_format(value: str | None) -> bool:
    normalized = _normalize_tracking_number(value)
    return bool(_CH_WAYBILL_CANDIDATE_RE.fullmatch(normalized) and not _CH_WAYBILL_FULL_RE.fullmatch(normalized))


def _bounded_int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _negative_cache_ttl_seconds() -> int:
    return _bounded_int_env("WEBCHAT_FAST_TRACKING_NEGATIVE_CACHE_SECONDS", 60, minimum=0, maximum=300)


def _negative_cache_max_entries() -> int:
    return _bounded_int_env("WEBCHAT_FAST_TRACKING_NEGATIVE_CACHE_MAX_ENTRIES", 2048, minimum=32, maximum=20000)


def _negative_cache_key(tracking_number: str | None) -> str | None:
    normalized = _normalize_tracking_number(tracking_number)
    return _wf.hash_tracking_number(normalized) if normalized else None


def _prune_negative_cache(now: float) -> None:
    expired = [key for key, (expires_at, _reason, _status) in _NEGATIVE_CACHE.items() if expires_at <= now]
    for key in expired:
        _NEGATIVE_CACHE.pop(key, None)
    max_entries = _negative_cache_max_entries()
    overflow = len(_NEGATIVE_CACHE) - max_entries
    if overflow > 0:
        for key in list(_NEGATIVE_CACHE.keys())[:overflow]:
            _NEGATIVE_CACHE.pop(key, None)


def _store_negative_cache(cache_key: str, *, expires_at: float, failure_reason: str | None, tool_status: str | None) -> None:
    _NEGATIVE_CACHE[cache_key] = (expires_at, failure_reason, tool_status)
    _prune_negative_cache(time.monotonic())


def _tracking_no_evidence_result(*, tracking_number: str | None, failure_reason: str, tool_status: str) -> _wf.TrackingFactResult:
    return _wf.TrackingFactResult(
        ok=False,
        tracking_number=_normalize_tracking_number(tracking_number) or tracking_number,
        tool_status=tool_status,
        pii_redacted=True,
        fact_evidence_present=False,
        failure_reason=failure_reason,
    )


def _cacheable_negative_result(result: _wf.TrackingFactResult | None) -> bool:
    if result is None or result.fact_evidence_present or not result.pii_redacted:
        return False
    reason = str(result.failure_reason or "").strip().lower()
    status = str(result.tool_status or "").strip().lower()
    if reason in _NON_CACHEABLE_FAILURES or status in _NON_CACHEABLE_FAILURES:
        return False
    return bool(reason or status)


def _sanitize_public_string(value: str, *, key: str = "") -> str:
    if not value:
        return value
    lowered_key = key.lower()
    if "hash" in lowered_key or value.startswith("sha256:"):
        return value
    redacted = _CH_RAW_TOKEN_RE.sub(_REDACTION, value)
    redacted = _LONG_NUMERIC_RE.sub(_REDACTION, redacted)
    return redacted


def _sanitize_public_payload(value: Any, *, key: str = "", in_trace: bool = False) -> Any:
    trace_scope = in_trace or key in _TRACE_KEYS
    if isinstance(value, dict):
        return {
            str(item_key): _sanitize_public_payload(
                item_value,
                key=str(item_key),
                in_trace=trace_scope,
            )
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_public_payload(item, key=key, in_trace=trace_scope) for item in value]
    if isinstance(value, str) and trace_scope:
        return _sanitize_public_string(value, key=key)
    return value


_ORIGINAL_LOOKUP_FAST_TRACKING_FACT = _wf._lookup_fast_tracking_fact
_ORIGINAL_MARK_WEBCHAT_FAST_DONE = _wf.mark_webchat_fast_done
_ORIGINAL_WITH_FAST_PUBLIC_SESSION = _wf._with_fast_public_session


def _lookup_fast_tracking_fact_guarded(
    *,
    tracking_number: str | None,
    conversation_id: int | None,
    ticket_id: int | None,
    request_id: str | None,
    caller_id: str | None = None,
    country_code: str | None = None,
) -> _wf.TrackingFactResult | None:
    if tracking_number and _is_invalid_ch_waybill_format(tracking_number):
        return _tracking_no_evidence_result(
            tracking_number=tracking_number,
            failure_reason="invalid_ch_waybill_format",
            tool_status="format_invalid",
        )

    cache_key = _negative_cache_key(tracking_number)
    now = time.monotonic()
    ttl = _negative_cache_ttl_seconds()
    if cache_key and ttl > 0:
        cached = _NEGATIVE_CACHE.get(cache_key)
        if cached:
            expires_at, failure_reason, tool_status = cached
            if expires_at > now:
                return _tracking_no_evidence_result(
                    tracking_number=tracking_number,
                    failure_reason=failure_reason or "tracking_fact_negative_cache_hit",
                    tool_status=tool_status or "negative_cache_hit",
                )
            _NEGATIVE_CACHE.pop(cache_key, None)

    result = _ORIGINAL_LOOKUP_FAST_TRACKING_FACT(
        tracking_number=tracking_number,
        conversation_id=conversation_id,
        ticket_id=ticket_id,
        request_id=request_id,
        caller_id=caller_id,
        country_code=country_code,
    )
    if cache_key and ttl > 0 and _cacheable_negative_result(result):
        _store_negative_cache(
            cache_key,
            expires_at=now + ttl,
            failure_reason=result.failure_reason,
            tool_status=result.tool_status,
        )
    return result


def _mark_webchat_fast_done_redacted(db, row, *, response_json: dict[str, Any]) -> None:
    _ORIGINAL_MARK_WEBCHAT_FAST_DONE(db, row, response_json=_sanitize_public_payload(response_json))


def _with_fast_public_session_redacted(db, conversation, payload: dict[str, Any]) -> dict[str, Any]:
    sanitized_payload = _sanitize_public_payload(payload)
    return _sanitize_public_payload(_ORIGINAL_WITH_FAST_PUBLIC_SESSION(db, conversation, sanitized_payload))


if not getattr(_wf, "_public_trace_redaction_precheck_patch_applied", False):
    _wf._lookup_fast_tracking_fact = _lookup_fast_tracking_fact_guarded
    _wf.mark_webchat_fast_done = _mark_webchat_fast_done_redacted
    _wf._with_fast_public_session = _with_fast_public_session_redacted
    _wf._public_trace_redaction_precheck_patch_applied = True
