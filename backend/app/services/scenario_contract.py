from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from ..utils.time import ensure_utc, utc_now
from .nexus_osr.business_scenarios import (
    BusinessScenarioCatalog,
    BusinessScenarioDefinition,
    load_business_scenario_catalog,
)


class ScenarioContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class FrozenScenarioContract:
    scenario: BusinessScenarioDefinition
    catalog_version: str
    catalog_sha256: str
    definition_json: dict[str, Any]
    definition_sha256: str


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def iso_utc(value: datetime | None) -> str | None:
    normalized = ensure_utc(value) if value is not None else None
    return normalized.isoformat() if normalized is not None else None


def scenario_definition_payload(
    scenario: BusinessScenarioDefinition,
) -> dict[str, Any]:
    return {
        "scenario_key": scenario.scenario_key,
        "issue_type_aliases": list(scenario.issue_type_aliases),
        "trigger_sources": list(scenario.trigger_sources),
        "required_fact_classes": list(scenario.required_fact_classes),
        "required_customer_inputs": list(scenario.required_customer_inputs),
        "risk_level": scenario.risk_level,
        "escalation_policy_key": scenario.escalation_policy_key,
        "owner_queue_key": scenario.owner_queue_key,
        "required_capabilities": list(scenario.required_capabilities),
        "allowed_action_classes": list(scenario.allowed_action_classes),
        "required_action_classes": list(scenario.required_action_classes),
        "blocked_action_classes": list(scenario.blocked_action_classes),
        "notification_policy": scenario.notification_policy,
        "allowed_no_notification_reasons": list(
            scenario.allowed_no_notification_reasons
        ),
        "terminal_behavior": scenario.terminal_behavior,
        "required_outcome_levels": list(scenario.required_outcome_levels),
        "completion_rules": list(scenario.completion_rules),
        "definition_of_done": scenario.definition_of_done,
        "observation_period_seconds": scenario.observation_period_seconds,
        "reopen_conditions": list(scenario.reopen_conditions),
        "cancellation_semantics": scenario.cancellation_semantics,
        "metrics": list(scenario.metrics),
        "scope_mode": scenario.scope_mode,
        "lifecycle": {
            "status": scenario.lifecycle.status,
            "owner": scenario.lifecycle.owner,
            "approved_at": iso_utc(scenario.lifecycle.approved_at),
            "effective_from": iso_utc(scenario.lifecycle.effective_from),
            "review_due": iso_utc(scenario.lifecycle.review_due),
            "expires_at": iso_utc(scenario.lifecycle.expires_at),
            "supersedes": scenario.lifecycle.supersedes,
        },
    }


def scenario_is_operationally_active(
    scenario: BusinessScenarioDefinition,
    *,
    at: datetime | None = None,
) -> bool:
    """Review due is a governance warning; expires_at is the stop control."""

    observed = ensure_utc(at or utc_now())
    if observed is None:
        return False
    lifecycle = scenario.lifecycle
    return bool(
        lifecycle.status == "approved"
        and lifecycle.effective_from <= observed
        and (lifecycle.expires_at is None or observed < lifecycle.expires_at)
    )


def current_scenario_catalog() -> BusinessScenarioCatalog:
    return load_business_scenario_catalog(require_all_active=False)


def resolve_catalog_scenario(
    catalog: BusinessScenarioCatalog,
    scenario_key: str,
    *,
    at: datetime | None = None,
) -> BusinessScenarioDefinition:
    normalized = str(scenario_key or "").strip().lower()
    target = catalog.alias_map().get(normalized)
    if target is None:
        raise ScenarioContractError("scenario_not_found")
    scenario = catalog.by_key()[target]
    if not scenario_is_operationally_active(scenario, at=at):
        raise ScenarioContractError("scenario_not_operationally_active")
    return scenario


def legacy_alias_matches(
    *,
    values: Iterable[tuple[str, object]],
    catalog: BusinessScenarioCatalog,
) -> tuple[set[str], list[str]]:
    matched: set[str] = set()
    observed: list[str] = []
    aliases = catalog.alias_map()
    for field_name, raw in values:
        normalized = str(raw or "").strip().lower()
        if not normalized:
            continue
        observed.append(f"{field_name}:{normalized}")
        target = aliases.get(normalized)
        if target is not None:
            matched.add(target)
    return matched, observed


def freeze_scenario(
    catalog: BusinessScenarioCatalog,
    scenario: BusinessScenarioDefinition,
) -> FrozenScenarioContract:
    definition = scenario_definition_payload(scenario)
    return FrozenScenarioContract(
        scenario=scenario,
        catalog_version=catalog.catalog_version,
        catalog_sha256=catalog.source_sha256,
        definition_json=definition,
        definition_sha256=sha256_json(definition),
    )


__all__ = [
    "FrozenScenarioContract",
    "ScenarioContractError",
    "canonical_json",
    "current_scenario_catalog",
    "freeze_scenario",
    "iso_utc",
    "legacy_alias_matches",
    "resolve_catalog_scenario",
    "scenario_definition_payload",
    "scenario_is_operationally_active",
    "sha256_json",
]
