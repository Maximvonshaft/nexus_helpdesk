from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any, Callable

from ...settings import get_settings
from ...utils.time import utc_now
from . import runtime as _runtime
from .vector_contract import KNOWLEDGE_VECTOR_DIMENSION, validate_embedding_dimension

LIVE_TRACKING_TERMS = {
    "tracking", "track", "waybill", "parcel", "package", "shipment",
    "物流", "运单", "单号", "包裹", "快递", "查件",
}
LIVE_STATUS_TERMS = {
    "where", "status", "current", "now", "delivered", "arrived", "location",
    "在哪", "哪里", "状态", "现在", "到了", "签收", "派送到",
}
POLICY_TERMS = {
    "format", "example", "policy", "rule", "how to", "what is",
    "格式", "示例", "规则", "政策", "怎么查", "如何查询",
}
TRACKING_ID_RE = re.compile(r"(?<![A-Z0-9])[A-Z0-9][A-Z0-9-]{7,34}[A-Z0-9](?![A-Z0-9])", re.IGNORECASE)

_INSTALLED = False
_ORIGINAL_RETRIEVE: Callable[..., Any] | None = None
_ORIGINAL_CANDIDATES: Callable[..., Any] | None = None


def _normalize(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def is_live_tracking_intent(value: str | None) -> bool:
    text = _normalize(value)
    if not text:
        return False
    has_tracking = any(term in text for term in LIVE_TRACKING_TERMS)
    has_status = any(term in text for term in LIVE_STATUS_TERMS)
    has_identifier = any(any(char.isdigit() for char in match.group(0)) for match in TRACKING_ID_RE.finditer((value or "").upper()))
    policy_only = any(term in text for term in POLICY_TERMS) and not has_identifier
    return has_tracking and (has_status or has_identifier) and not policy_only


def _as_utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _in_window(now: datetime, *pairs: tuple[Any, Any]) -> bool:
    for start, end in pairs:
        start_value = _as_utc(start)
        end_value = _as_utc(end)
        if start_value is not None and now < start_value:
            return False
        if end_value is not None and now >= end_value:
            return False
    return True


def _scope(value: Any, default: str) -> str:
    return str(value or default).strip()


def _eligible(
    chunk: Any,
    item: Any,
    *,
    now: datetime,
    tenant_id: str,
    brand_id: str,
    country_scope: str,
    channel_scope: str,
    channel: str | None,
    audience_scope: str,
) -> bool:
    if (item.status or "").lower() != "active" or (chunk.status or "").lower() != "active":
        return False
    if int(item.published_version or 0) <= 0 or item.published_at is None:
        return False
    if int(chunk.published_version or 0) != int(item.published_version or 0):
        return False
    if _scope(item.tenant_id, "default") != tenant_id or _scope(chunk.tenant_id, "default") != tenant_id:
        return False
    if _scope(item.brand_id, "default") != brand_id or _scope(chunk.brand_id, "default") != brand_id:
        return False

    requested_country = country_scope.upper()
    for value in (item.country_scope, chunk.country_scope):
        if _scope(value, _runtime.GLOBAL_COUNTRY_SCOPE).upper() not in {requested_country, _runtime.GLOBAL_COUNTRY_SCOPE}:
            return False

    requested_channel = channel_scope.lower()
    for value in (item.channel_scope, chunk.channel_scope):
        if _scope(value, _runtime.GLOBAL_CHANNEL_SCOPE).lower() not in {requested_channel, _runtime.GLOBAL_CHANNEL_SCOPE}:
            return False
    if channel:
        requested_exact = channel.strip().lower()
        for value in (item.channel, chunk.channel):
            if value and str(value).strip().lower() not in {requested_exact, _runtime.GLOBAL_CHANNEL_SCOPE}:
                return False

    for value in (item.audience_scope, chunk.audience_scope):
        if _scope(value, "customer").lower() != audience_scope.lower():
            return False
    for value in (item.visibility, chunk.visibility):
        if _scope(value, "internal").lower() != _runtime.CUSTOMER_VISIBILITY:
            return False
    for value in (item.shareability, chunk.shareability):
        if _scope(value, "internal").lower() != "customer_visible":
            return False

    if not _in_window(
        now,
        (item.valid_from, item.valid_until),
        (item.starts_at, item.ends_at),
        (chunk.valid_from, chunk.valid_until),
        (chunk.starts_at, chunk.ends_at),
    ):
        return False

    kind = (item.knowledge_kind or "document").lower()
    if kind in _runtime.STRUCTURED_KINDS:
        if (item.fact_status or "draft").lower() != "approved":
            return False
        if (chunk.fact_status or item.fact_status or "draft").lower() != "approved":
            return False
    return True


def _not_ready(reason: str, *, started: float) -> Any:
    return _runtime.KnowledgeRuntimeResult(
        hits=[],
        direct_facts=[],
        locked_facts=[],
        confidence=0.0,
        no_answer_reason=reason,
        trace={
            "runtime": "knowledge_data_safety_v1",
            "decision": "blocked",
            "reason": reason,
        },
        retrieval_methods=[],
        latency_ms=max(0, int((time.monotonic() - started) * 1000)),
    )


def retrieve_knowledge_safe(*args: Any, **kwargs: Any):
    if _ORIGINAL_RETRIEVE is None:
        raise RuntimeError("knowledge data safety guard is not installed")
    started = time.monotonic()
    if is_live_tracking_intent(kwargs.get("query")):
        return _not_ready("live_tracking_requires_truth_source", started=started)
    settings = get_settings()
    if settings.knowledge_embeddings_enabled:
        try:
            validate_embedding_dimension(settings.knowledge_embedding_dim)
        except ValueError:
            return _not_ready("knowledge_vector_dimension_mismatch", started=started)
    return _ORIGINAL_RETRIEVE(*args, **kwargs)


def candidate_rows_safe(
    db: Any,
    *,
    terms: list[str],
    normalized_query: str,
    tenant_id: str,
    brand_id: str,
    country_scope: str,
    channel_scope: str,
    market_id: int | None,
    channel: str | None,
    audience_scope: str,
    language: str | None,
):
    if _ORIGINAL_CANDIDATES is None:
        raise RuntimeError("knowledge data safety guard is not installed")
    rows = _ORIGINAL_CANDIDATES(
        db,
        terms=terms,
        normalized_query=normalized_query,
        tenant_id=tenant_id,
        brand_id=brand_id,
        country_scope=country_scope,
        channel_scope=channel_scope,
        market_id=market_id,
        channel=channel,
        audience_scope=audience_scope,
        language=language,
    )
    now = _as_utc(utc_now()) or datetime.now(timezone.utc)
    return [
        (chunk, item)
        for chunk, item in rows
        if _eligible(
            chunk,
            item,
            now=now,
            tenant_id=tenant_id,
            brand_id=brand_id,
            country_scope=country_scope,
            channel_scope=channel_scope,
            channel=channel,
            audience_scope=audience_scope,
        )
    ]


def install() -> None:
    global _INSTALLED, _ORIGINAL_RETRIEVE, _ORIGINAL_CANDIDATES
    if _INSTALLED:
        return
    _ORIGINAL_RETRIEVE = _runtime.retrieve_knowledge
    _ORIGINAL_CANDIDATES = _runtime._candidate_rows
    _runtime.retrieve_knowledge = retrieve_knowledge_safe
    _runtime._candidate_rows = candidate_rows_safe
    _INSTALLED = True
