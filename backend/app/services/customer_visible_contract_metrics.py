from __future__ import annotations

from typing import Any

from .observability import LOGGER

try:  # metrics must never break the message path
    from . import observability as _obs

    Counter = getattr(_obs, "Counter", None)
    _PROM_REGISTRY = getattr(_obs, "_PROM_REGISTRY", None)
    _label = getattr(_obs, "_label", None)
except Exception:  # pragma: no cover
    Counter = None
    _PROM_REGISTRY = None
    _label = None


def _safe_label(value: Any, default: str = "unknown") -> str:
    if callable(_label):
        try:
            return _label(value, default)  # type: ignore[misc]
        except Exception:
            pass
    cleaned = str(value or default).strip() or default
    return cleaned[:80]


def _counter(name: str, description: str, labels: list[str]):
    if Counter is None:
        return None
    try:
        return Counter(name, description, labels, registry=_PROM_REGISTRY)
    except Exception:  # pragma: no cover
        return None


_CUSTOMER_VISIBLE_CONTRACT_BLOCK = _counter(
    "customer_visible_contract_block_total",
    "Customer-visible outbound contract blocks by reason, origin, channel, and contract version.",
    ["reason", "origin", "channel", "contract_version"],
)
_RUNTIME_SIGNED_BODY_MUTATION = _counter(
    "runtime_signed_body_mutation_total",
    "Signed AI outbound body mutation attempts after runtime signature.",
    ["channel", "origin", "contract_version"],
)
_RUNTIME_CONTRACT_PAYLOAD_HASH_MISMATCH = _counter(
    "runtime_contract_payload_hash_mismatch_total",
    "Runtime contract payload hash mismatches.",
    ["channel", "origin", "contract_version"],
)
_AI_REPLY_V3_VALIDATION_FAILED = _counter(
    "ai_reply_v3_validation_failed_total",
    "AI reply v3 validation failures.",
    ["reason", "reply_type", "channel"],
)
_AI_REPLY_SENT = _counter(
    "ai_reply_sent_total",
    "AI customer-visible replies sent by channel, contract version, reply type, and origin.",
    ["channel", "contract_version", "reply_type", "origin"],
)
_RUNTIME_NULL_REPLY = _counter(
    "runtime_null_reply_total",
    "Runtime null replies that did not produce customer-visible text.",
    ["channel", "reason"],
)
_HANDOFF_REQUESTED = _counter(
    "handoff_requested_total",
    "Handoff requests by channel, reason, and triggering actor.",
    ["channel", "reason", "triggered_by"],
)
_HUMAN_TAKEOVER = _counter(
    "human_takeover_total",
    "Human takeover transitions by channel and previous state.",
    ["channel", "previous_state"],
)
_MISSING_CUSTOMER_VISIBLE_ORIGIN_CONTRACT = _counter(
    "missing_customer_visible_origin_contract_total",
    "Originless customer-visible outbound blocks by channel and row status.",
    ["channel", "status"],
)
_KNOWLEDGE_RETRIEVAL_EFFECTIVE_COUNTRY = _counter(
    "knowledge_retrieval_effective_country_total",
    "Knowledge retrieval distribution by effective country, source, and channel.",
    ["effective_country", "country_source", "channel"],
)
_KNOWLEDGE_HIT = _counter(
    "knowledge_hit_total",
    "Knowledge hits by channel, effective country, authority level, and source type.",
    ["channel", "effective_country", "authority_level", "source_type"],
)
_KNOWLEDGE_NO_HIT = _counter(
    "knowledge_no_hit_total",
    "Knowledge no-hit count by channel, effective country, and intent.",
    ["channel", "effective_country", "intent"],
)
_AI_HANDOFF_AFTER_KNOWLEDGE_HIT = _counter(
    "ai_handoff_after_knowledge_hit_total",
    "AI handoff after a knowledge hit by effective country, authority, and handoff reason.",
    ["effective_country", "authority_level", "handoff_reason"],
)


def _inc(counter, labels: dict[str, Any], *, event: str, context: dict[str, Any] | None = None) -> None:
    try:
        safe_labels = {key: _safe_label(value) for key, value in labels.items()}
        if counter is not None:
            counter.labels(**safe_labels).inc()
        LOGGER.info(event, extra={"event_payload": {**safe_labels, **(context or {})}})
    except Exception as exc:  # pragma: no cover - metrics/logging must never affect message flow
        try:
            LOGGER.warning("customer_visible_contract_metric_failed", extra={"event_payload": {"event": event, "error": type(exc).__name__}})
        except Exception:
            pass


def record_customer_visible_contract_block(*, reason: str | None, origin: str | None, channel: str | None, contract_version: str | None, ticket_id: int | None = None, conversation_id: int | None = None, outbound_id: int | None = None) -> None:
    _inc(
        _CUSTOMER_VISIBLE_CONTRACT_BLOCK,
        {"reason": reason, "origin": origin, "channel": channel, "contract_version": contract_version},
        event="customer_visible_contract_blocked",
        context={"ticket_id": ticket_id, "conversation_id": conversation_id, "outbound_id": outbound_id},
    )


def record_runtime_signed_body_mutation(*, channel: str | None, origin: str | None, contract_version: str | None, ticket_id: int | None = None, outbound_id: int | None = None) -> None:
    _inc(
        _RUNTIME_SIGNED_BODY_MUTATION,
        {"channel": channel, "origin": origin, "contract_version": contract_version},
        event="runtime_signed_body_mutation_blocked",
        context={"ticket_id": ticket_id, "outbound_id": outbound_id},
    )


def record_runtime_contract_payload_hash_mismatch(*, channel: str | None, origin: str | None, contract_version: str | None, ticket_id: int | None = None, outbound_id: int | None = None) -> None:
    _inc(
        _RUNTIME_CONTRACT_PAYLOAD_HASH_MISMATCH,
        {"channel": channel, "origin": origin, "contract_version": contract_version},
        event="runtime_contract_payload_hash_mismatch",
        context={"ticket_id": ticket_id, "outbound_id": outbound_id},
    )


def record_ai_reply_v3_validation_failed(*, reason: str | None, reply_type: str | None, channel: str | None, ticket_id: int | None = None, outbound_id: int | None = None) -> None:
    _inc(
        _AI_REPLY_V3_VALIDATION_FAILED,
        {"reason": reason, "reply_type": reply_type, "channel": channel},
        event="ai_reply_v3_validation_failed",
        context={"ticket_id": ticket_id, "outbound_id": outbound_id},
    )


def record_ai_reply_sent(*, channel: str | None, contract_version: str | None, reply_type: str | None, origin: str | None, ticket_id: int | None = None, outbound_id: int | None = None) -> None:
    _inc(
        _AI_REPLY_SENT,
        {"channel": channel, "contract_version": contract_version, "reply_type": reply_type, "origin": origin},
        event="ai_reply_sent",
        context={"ticket_id": ticket_id, "outbound_id": outbound_id},
    )


def record_runtime_null_reply(*, channel: str | None, reason: str | None, ticket_id: int | None = None, conversation_id: int | None = None) -> None:
    _inc(
        _RUNTIME_NULL_REPLY,
        {"channel": channel, "reason": reason},
        event="runtime_null_reply",
        context={"ticket_id": ticket_id, "conversation_id": conversation_id},
    )


def record_handoff_requested(*, channel: str | None, reason: str | None, triggered_by: str | None, ticket_id: int | None = None, conversation_id: int | None = None) -> None:
    _inc(
        _HANDOFF_REQUESTED,
        {"channel": channel, "reason": reason, "triggered_by": triggered_by},
        event="handoff_requested",
        context={"ticket_id": ticket_id, "conversation_id": conversation_id},
    )


def record_human_takeover(*, channel: str | None, previous_state: str | None, ticket_id: int | None = None, conversation_id: int | None = None) -> None:
    _inc(
        _HUMAN_TAKEOVER,
        {"channel": channel, "previous_state": previous_state},
        event="human_takeover",
        context={"ticket_id": ticket_id, "conversation_id": conversation_id},
    )


def record_missing_customer_visible_origin_contract(*, channel: str | None, status: str | None, ticket_id: int | None = None, outbound_id: int | None = None) -> None:
    _inc(
        _MISSING_CUSTOMER_VISIBLE_ORIGIN_CONTRACT,
        {"channel": channel, "status": status},
        event="missing_customer_visible_origin_contract",
        context={"ticket_id": ticket_id, "outbound_id": outbound_id},
    )


def record_knowledge_retrieval_effective_country(*, effective_country: str | None, country_source: str | None, channel: str | None, ticket_id: int | None = None, conversation_id: int | None = None) -> None:
    _inc(
        _KNOWLEDGE_RETRIEVAL_EFFECTIVE_COUNTRY,
        {"effective_country": effective_country, "country_source": country_source, "channel": channel},
        event="knowledge_retrieval_effective_country",
        context={"ticket_id": ticket_id, "conversation_id": conversation_id},
    )


def record_knowledge_hit(*, channel: str | None, effective_country: str | None, authority_level: str | None, source_type: str | None, ticket_id: int | None = None, conversation_id: int | None = None) -> None:
    _inc(
        _KNOWLEDGE_HIT,
        {"channel": channel, "effective_country": effective_country, "authority_level": authority_level, "source_type": source_type},
        event="knowledge_hit",
        context={"ticket_id": ticket_id, "conversation_id": conversation_id},
    )


def record_knowledge_no_hit(*, channel: str | None, effective_country: str | None, intent: str | None, ticket_id: int | None = None, conversation_id: int | None = None) -> None:
    _inc(
        _KNOWLEDGE_NO_HIT,
        {"channel": channel, "effective_country": effective_country, "intent": intent},
        event="knowledge_no_hit",
        context={"ticket_id": ticket_id, "conversation_id": conversation_id},
    )


def record_ai_handoff_after_knowledge_hit(*, effective_country: str | None, authority_level: str | None, handoff_reason: str | None, ticket_id: int | None = None, conversation_id: int | None = None) -> None:
    _inc(
        _AI_HANDOFF_AFTER_KNOWLEDGE_HIT,
        {"effective_country": effective_country, "authority_level": authority_level, "handoff_reason": handoff_reason},
        event="ai_handoff_after_knowledge_hit",
        context={"ticket_id": ticket_id, "conversation_id": conversation_id},
    )
