from __future__ import annotations

from collections import Counter
from typing import Iterable

from .registry import DomainRegistry, build_default_registry
from .rewrite import is_low_signal_query, normalize_query, rewrite_query
from .schemas import DomainEntity, DomainIntent, DomainQueryUnderstandingResult


def understand_query(
    query: str | None,
    *,
    registry: DomainRegistry | None = None,
    shadow_mode: bool = True,
) -> DomainQueryUnderstandingResult:
    registry = registry or build_default_registry()
    raw = query or ""
    normalized = normalize_query(raw)
    if is_low_signal_query(normalized):
        return DomainQueryUnderstandingResult(
            raw_query=raw,
            normalized_query=normalized,
            shadow_mode=shadow_mode,
        )

    matches = _match_intents(normalized, registry.all_intents())
    if not matches:
        return DomainQueryUnderstandingResult(
            raw_query=raw,
            normalized_query=normalized,
            shadow_mode=shadow_mode,
        )

    matches.sort(key=lambda item: (item[0], len(item[2])), reverse=True)
    top_score, primary, _alias = matches[0]
    secondary = tuple(intent.full_key for score, intent, _ in matches[1:6] if intent.full_key != primary.full_key and score > 0)
    matched_aliases = tuple(alias for _, _, alias in matches[:10])
    rewrite = rewrite_query(normalized, [primary, *(intent for _, intent, _ in matches[1:6])])
    entities = _extract_entities(normalized, primary)
    confidence = min(1.0, round(top_score / 100.0, 3))

    return DomainQueryUnderstandingResult(
        raw_query=raw,
        normalized_query=normalized,
        domain=primary.domain,
        primary_intent=primary.full_key,
        secondary_intents=secondary,
        entities=entities,
        rewrite=rewrite,
        evidence_class=primary.evidence_class,
        action_boundary=primary.action_boundary,
        allowed_plan_types=primary.allowed_plan_types,
        requires_verification=primary.requires_verification,
        requires_tool_boundary=primary.requires_tool_boundary,
        confidence=confidence,
        matched_aliases=matched_aliases,
        shadow_mode=shadow_mode,
    )


def _match_intents(normalized: str, intents: Iterable[DomainIntent]) -> list[tuple[float, DomainIntent, str]]:
    results: list[tuple[float, DomainIntent, str]] = []
    negative_hits = Counter()
    for intent in intents:
        for neg in intent.negative_aliases:
            marker = normalize_query(neg)
            if marker and marker in normalized:
                negative_hits[intent.full_key] += 1
    for intent in intents:
        if negative_hits[intent.full_key]:
            continue
        for alias in intent.aliases:
            marker = normalize_query(alias)
            if not marker:
                continue
            if marker in normalized:
                score = 70.0 + min(30.0, len(marker) / 2.0)
                results.append((score, intent, marker))
    return results


def _extract_entities(normalized: str, intent: DomainIntent) -> tuple[DomainEntity, ...]:
    entities: list[DomainEntity] = []
    entity_markers = {
        "tracking_number_present": ("waybill", "tracking", "单号", "运单"),
        "recipient_absent": ("not home", "not at home", "missed", "不在家", "没人", "无人"),
        "address_change_requested": ("change address", "update address", "wrong address", "改地址", "地址写错"),
        "complaint_requested": ("complain", "complaint", "投诉", "escalate"),
    }
    for key, markers in entity_markers.items():
        if any(marker in normalized for marker in markers):
            entities.append(DomainEntity(key=key, value=True, source="marker"))
    if intent.requires_verification:
        entities.append(DomainEntity(key="verification_required", value=True, source="intent"))
    if intent.requires_tool_boundary:
        entities.append(DomainEntity(key="tool_boundary_required", value=True, source="intent"))
    return tuple(entities)
