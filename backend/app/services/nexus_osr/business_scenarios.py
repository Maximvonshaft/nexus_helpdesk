from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..permissions import ALL_CAPABILITIES

CATALOG_SCHEMA = "nexus.business-scenario-catalog.v1"
DEFAULT_CATALOG_PATH = Path(__file__).resolve().parents[2] / "config" / "business_scenarios.v1.json"
MAX_CATALOG_BYTES = 1_000_000
MAX_SCENARIOS = 100
MAX_LIST_ITEMS = 50
KEY_RE = re.compile(r"^[a-z][a-z0-9_.:-]{1,159}$")

TRIGGERS = frozenset({"customer_message", "operator_input", "operational_signal", "system_failure"})
FACTS = frozenset({
    "parcel_identity", "tracking_current_status", "tracking_event_history", "service_commitment",
    "delivery_attempts", "address_contact", "proof_of_delivery", "case_history", "policy_entitlement",
    "transaction_state", "return_status", "cod_payment", "dispatch_status", "identity_verification",
})
FORBIDDEN_FACTS = frozenset({"customer_claim", "prior_ai_output", "ai_recommendation"})
RISKS = frozenset({"low", "medium", "high", "critical"})
NOTIFICATION_POLICIES = frozenset({"required", "required_if_contactable", "optional", "prohibited"})
NOTIFICATION_WAIVER_REASONS = frozenset({
    "no_contact_method", "contact_prohibited", "legal_hold", "no_customer_impact",
})
CANCELLATION_SEMANTICS = frozenset({
    "cancel_only_with_reason",
    "cancel_only_before_external_acceptance",
    "cancel_only_with_supervisor_reason",
    "cannot_cancel_after_authorized_decision",
    "cannot_cancel_after_escalation",
    "cancel_only_before_return_acceptance",
    "cannot_cancel_after_financial_posting",
    "cancel_only_before_repair_acceptance",
    "cancel_after_reclassification",
})
LIFECYCLE_STATUSES = frozenset({"draft", "approved", "archived"})
SCOPE_MODES = frozenset({"inherit_resolved_scope"})
TERMINAL_BEHAVIORS = frozenset({"closeable", "reclassify_only", "escalate_only"})
OUTCOMES = frozenset({
    "accepted", "technical_completed", "operational_completed",
    "customer_notified", "business_result_confirmed",
})
COMPLETION_RULES = frozenset({
    "required_facts_present", "required_customer_inputs_present", "required_actions_completed",
    "required_outcomes_completed", "notification_policy_satisfied", "no_open_high_risk_escalation",
    "no_repair_required", "observation_period_elapsed_if_required", "scenario_reclassified",
})
ACTIONS = frozenset({
    "tracking_lookup", "ask_customer_information", "handoff", "create_ticket", "internal_note",
    "notify_customer", "create_delivery_work_order", "update_address_contact", "cancel_order",
    "request_proof_of_delivery", "schedule_redelivery", "dispatch_operations", "refund_review",
    "compensation_review", "legal_escalation", "privacy_request", "return_process", "cod_investigation",
    "retry_dispatch", "repair_dispatch", "scenario_reclassify",
})
REOPEN_CONDITIONS = frozenset({
    "customer_recontacts_within_observation", "new_authoritative_contradiction", "required_action_failed",
    "delivery_failure_after_intervention", "customer_disputes_resolution", "dispatch_repair_required",
    "new_material_evidence",
})
METRICS = frozenset({
    "scenario_completion_rate", "safe_effective_closure_rate", "first_contact_resolution_rate",
    "reopen_72h_rate", "reopen_7d_rate", "repeat_contact_rate", "customer_notification_compliance",
    "action_operational_completion_rate", "business_result_confirmation_rate", "false_closure_rate",
    "repair_required_rate", "average_resolution_seconds", "human_touches_per_case", "duplicate_case_rate",
    "duplicate_action_prevention_rate",
})
AUTHORITATIVE_CAPABILITIES = frozenset(str(value).strip().lower() for value in ALL_CAPABILITIES)

CATALOG_FIELDS = frozenset({"schema", "catalog_version", "owner", "approved_at", "scope_mode", "scenarios"})
LIFECYCLE_FIELDS = frozenset({
    "status", "owner", "approved_at", "effective_from", "review_due", "expires_at", "supersedes",
})
SCENARIO_FIELDS = frozenset({
    "scenario_key", "issue_type_aliases", "trigger_sources", "required_fact_classes",
    "required_customer_inputs", "risk_level", "escalation_policy_key", "owner_queue_key",
    "required_capabilities", "allowed_action_classes", "required_action_classes", "blocked_action_classes",
    "notification_policy", "allowed_no_notification_reasons", "terminal_behavior",
    "required_outcome_levels", "completion_rules", "definition_of_done",
    "observation_period_seconds", "reopen_conditions", "cancellation_semantics",
    "metrics", "scope_mode", "lifecycle",
})


class BusinessScenarioCatalogError(ValueError):
    """Bounded fail-closed catalog error."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ScenarioLifecycle:
    status: str
    owner: str
    approved_at: datetime
    effective_from: datetime
    review_due: datetime
    expires_at: datetime | None
    supersedes: str | None

    def is_active(self, at: datetime) -> bool:
        return (
            self.status == "approved"
            and self.effective_from <= at < self.review_due
            and (self.expires_at is None or at < self.expires_at)
        )


@dataclass(frozen=True)
class BusinessScenarioDefinition:
    scenario_key: str
    issue_type_aliases: tuple[str, ...]
    trigger_sources: tuple[str, ...]
    required_fact_classes: tuple[str, ...]
    required_customer_inputs: tuple[str, ...]
    risk_level: str
    escalation_policy_key: str | None
    owner_queue_key: str
    required_capabilities: tuple[str, ...]
    allowed_action_classes: tuple[str, ...]
    required_action_classes: tuple[str, ...]
    blocked_action_classes: tuple[str, ...]
    notification_policy: str
    allowed_no_notification_reasons: tuple[str, ...]
    terminal_behavior: str
    required_outcome_levels: tuple[str, ...]
    completion_rules: tuple[str, ...]
    definition_of_done: str
    observation_period_seconds: int
    reopen_conditions: tuple[str, ...]
    cancellation_semantics: str
    metrics: tuple[str, ...]
    scope_mode: str
    lifecycle: ScenarioLifecycle

    def is_active(self, at: datetime | None = None) -> bool:
        return self.lifecycle.is_active(_utc(at))

    def safe_summary(self) -> dict[str, Any]:
        return {
            "scenario_key": self.scenario_key,
            "risk_level": self.risk_level,
            "owner_queue_key": self.owner_queue_key,
            "notification_policy": self.notification_policy,
            "terminal_behavior": self.terminal_behavior,
            "required_fact_classes": list(self.required_fact_classes),
            "required_action_classes": list(self.required_action_classes),
            "required_outcome_levels": list(self.required_outcome_levels),
            "observation_period_seconds": self.observation_period_seconds,
            "lifecycle_status": self.lifecycle.status,
        }


@dataclass(frozen=True)
class ScenarioReadiness:
    scenario_key: str
    closure_ready: bool
    missing_fact_classes: tuple[str, ...]
    missing_customer_inputs: tuple[str, ...]
    missing_action_classes: tuple[str, ...]
    missing_outcome_levels: tuple[str, ...]
    notification_satisfied: bool
    blocked_reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "scenario_key": self.scenario_key,
            "closure_ready": self.closure_ready,
            "missing_fact_classes": list(self.missing_fact_classes),
            "missing_customer_inputs": list(self.missing_customer_inputs),
            "missing_action_classes": list(self.missing_action_classes),
            "missing_outcome_levels": list(self.missing_outcome_levels),
            "notification_satisfied": self.notification_satisfied,
            "blocked_reasons": list(self.blocked_reasons),
        }


@dataclass(frozen=True)
class BusinessScenarioCatalog:
    schema: str
    catalog_version: str
    owner: str
    approved_at: datetime
    scope_mode: str
    scenarios: tuple[BusinessScenarioDefinition, ...]
    source_sha256: str

    def by_key(self) -> dict[str, BusinessScenarioDefinition]:
        return {item.scenario_key: item for item in self.scenarios}

    def alias_map(self) -> dict[str, str]:
        return {
            alias: item.scenario_key
            for item in self.scenarios
            for alias in (item.scenario_key, *item.issue_type_aliases)
        }

    def safe_summary(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "catalog_version": self.catalog_version,
            "owner": self.owner,
            "approved_at": self.approved_at.isoformat(),
            "scope_mode": self.scope_mode,
            "scenario_count": len(self.scenarios),
            "scenario_keys": [item.scenario_key for item in self.scenarios],
            "source_sha256": self.source_sha256,
        }


def load_business_scenario_catalog(
    path: str | Path | None = None,
    *,
    at: datetime | None = None,
    require_all_active: bool = True,
) -> BusinessScenarioCatalog:
    catalog_path = Path(path) if path is not None else DEFAULT_CATALOG_PATH
    try:
        raw = catalog_path.read_bytes()
    except OSError as exc:
        raise BusinessScenarioCatalogError("scenario_catalog_unavailable") from exc
    if not raw or len(raw) > MAX_CATALOG_BYTES:
        raise BusinessScenarioCatalogError("scenario_catalog_size_invalid")
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except BusinessScenarioCatalogError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BusinessScenarioCatalogError("scenario_catalog_json_invalid") from exc
    catalog = parse_business_scenario_catalog(
        payload,
        source_sha256=hashlib.sha256(raw).hexdigest(),
    )
    if require_all_active and any(not item.is_active(at) for item in catalog.scenarios):
        raise BusinessScenarioCatalogError("scenario_catalog_contains_inactive_definition")
    return catalog


def parse_business_scenario_catalog(
    payload: Any,
    *,
    source_sha256: str = "in_memory",
) -> BusinessScenarioCatalog:
    root = _mapping(payload, "scenario_catalog_root_invalid")
    _exact_keys(root, CATALOG_FIELDS, "scenario_catalog_fields_invalid")
    if root["schema"] != CATALOG_SCHEMA:
        raise BusinessScenarioCatalogError("scenario_catalog_schema_unsupported")
    rows = _sequence(root["scenarios"], "scenario_catalog_scenarios_invalid")
    if not rows or len(rows) > MAX_SCENARIOS:
        raise BusinessScenarioCatalogError("scenario_catalog_scenario_count_invalid")
    scenarios = tuple(_parse_scenario(row) for row in rows)
    _validate_unique_catalog(scenarios)
    approved_at = _timestamp(root["approved_at"], "scenario_catalog_approved_at_invalid")
    if any(item.lifecycle.approved_at > approved_at for item in scenarios):
        raise BusinessScenarioCatalogError("scenario_approved_after_catalog")
    return BusinessScenarioCatalog(
        schema=CATALOG_SCHEMA,
        catalog_version=_key(root["catalog_version"], "scenario_catalog_version_invalid"),
        owner=_label(root["owner"], "scenario_catalog_owner_invalid"),
        approved_at=approved_at,
        scope_mode=_choice(
            root["scope_mode"],
            SCOPE_MODES,
            "scenario_catalog_scope_mode_invalid",
        ),
        scenarios=scenarios,
        source_sha256=_label(
            source_sha256,
            "scenario_catalog_sha_invalid",
            limit=96,
        ),
    )


def resolve_business_scenario(
    catalog: BusinessScenarioCatalog,
    *,
    scenario_key: str | None = None,
    issue_type: str | None = None,
    at: datetime | None = None,
) -> BusinessScenarioDefinition:
    if not scenario_key and not issue_type:
        raise BusinessScenarioCatalogError("scenario_identity_required")
    aliases = catalog.alias_map()
    resolved: set[str] = set()
    for raw, reason in (
        (scenario_key, "scenario_key_invalid"),
        (issue_type, "scenario_issue_type_invalid"),
    ):
        if raw:
            target = aliases.get(_key(raw, reason))
            if target is None:
                raise BusinessScenarioCatalogError("scenario_not_found")
            resolved.add(target)
    if len(resolved) != 1:
        raise BusinessScenarioCatalogError("scenario_identity_conflict")
    scenario = catalog.by_key()[next(iter(resolved))]
    if not scenario.is_active(at):
        raise BusinessScenarioCatalogError("scenario_not_active")
    return scenario


def evaluate_scenario_readiness(
    scenario: BusinessScenarioDefinition,
    *,
    available_fact_classes: Iterable[str] = (),
    available_customer_inputs: Iterable[str] = (),
    completed_action_classes: Iterable[str] = (),
    completed_outcome_levels: Iterable[str] = (),
    customer_notification_state: str = "not_required",
    observation_period_elapsed: bool | None = None,
    repair_required: bool = False,
    open_high_risk_escalation: bool = False,
) -> ScenarioReadiness:
    facts = _normalized_values(available_fact_classes)
    inputs = _normalized_values(available_customer_inputs)
    actions = _normalized_values(completed_action_classes)
    outcomes = _normalized_values(completed_outcome_levels)
    missing_facts = tuple(item for item in scenario.required_fact_classes if item not in facts)
    missing_inputs = tuple(item for item in scenario.required_customer_inputs if item not in inputs)
    missing_actions = tuple(item for item in scenario.required_action_classes if item not in actions)
    missing_outcomes = tuple(item for item in scenario.required_outcome_levels if item not in outcomes)
    notification_ok = _notification_satisfied(
        scenario.notification_policy,
        customer_notification_state,
        scenario.allowed_no_notification_reasons,
    )
    blocked: list[str] = []
    for condition, reason in (
        (missing_facts, "required_facts_missing"),
        (missing_inputs, "required_customer_inputs_missing"),
        (missing_actions, "required_actions_missing"),
        (missing_outcomes, "required_outcomes_missing"),
        (not notification_ok, "notification_requirement_unsatisfied"),
        (
            scenario.observation_period_seconds > 0
            and observation_period_elapsed is not True,
            "observation_period_not_elapsed",
        ),
        (repair_required, "repair_required"),
        (open_high_risk_escalation, "high_risk_escalation_open"),
        (
            scenario.terminal_behavior != "closeable",
            f"terminal_behavior_{scenario.terminal_behavior}",
        ),
    ):
        if condition:
            blocked.append(reason)
    return ScenarioReadiness(
        scenario_key=scenario.scenario_key,
        closure_ready=not blocked,
        missing_fact_classes=missing_facts,
        missing_customer_inputs=missing_inputs,
        missing_action_classes=missing_actions,
        missing_outcome_levels=missing_outcomes,
        notification_satisfied=notification_ok,
        blocked_reasons=tuple(blocked),
    )


def _parse_scenario(payload: Any) -> BusinessScenarioDefinition:
    row = _mapping(payload, "scenario_definition_invalid")
    _exact_keys(row, SCENARIO_FIELDS, "scenario_definition_fields_invalid")

    raw_facts = _sequence(row["required_fact_classes"], "scenario_fact_classes_invalid")
    normalized_facts = tuple(str(item or "").strip().lower() for item in raw_facts)
    if set(normalized_facts) & FORBIDDEN_FACTS:
        raise BusinessScenarioCatalogError("scenario_fact_authority_forbidden")

    required_facts = _choices(
        raw_facts,
        FACTS,
        "scenario_fact_classes_invalid",
        allow_empty=True,
    )
    allowed_actions = _choices(
        row["allowed_action_classes"],
        ACTIONS,
        "scenario_allowed_actions_invalid",
    )
    required_actions = _choices(
        row["required_action_classes"],
        ACTIONS,
        "scenario_required_actions_invalid",
        allow_empty=True,
    )
    blocked_actions = _choices(
        row["blocked_action_classes"],
        ACTIONS,
        "scenario_blocked_actions_invalid",
        allow_empty=True,
    )
    if not set(required_actions).issubset(allowed_actions):
        raise BusinessScenarioCatalogError("scenario_required_action_not_allowed")
    if set(allowed_actions) & set(blocked_actions):
        raise BusinessScenarioCatalogError("scenario_action_conflict")

    notification = _choice(
        row["notification_policy"],
        NOTIFICATION_POLICIES,
        "scenario_notification_policy_invalid",
    )
    no_notification = _choices(
        row["allowed_no_notification_reasons"],
        NOTIFICATION_WAIVER_REASONS,
        "scenario_no_notification_reasons_invalid",
        allow_empty=True,
    )
    if notification == "required" and no_notification:
        raise BusinessScenarioCatalogError("scenario_required_notification_has_bypass")
    if notification == "required_if_contactable" and not no_notification:
        raise BusinessScenarioCatalogError("scenario_conditional_notification_reasons_missing")
    if notification in {"optional", "prohibited"} and no_notification:
        raise BusinessScenarioCatalogError("scenario_notification_waiver_not_applicable")

    scenario_key = _key(row["scenario_key"], "scenario_key_invalid")
    aliases = _keys(row["issue_type_aliases"], "scenario_aliases_invalid")
    if scenario_key in aliases:
        raise BusinessScenarioCatalogError("scenario_alias_repeats_key")

    required_inputs = _keys(
        row["required_customer_inputs"],
        "scenario_customer_inputs_invalid",
        allow_empty=True,
    )
    risk = _choice(
        row["risk_level"],
        RISKS,
        "scenario_risk_level_invalid",
    )
    escalation = _optional_key(
        row["escalation_policy_key"],
        "scenario_escalation_policy_invalid",
    )
    terminal = _choice(
        row["terminal_behavior"],
        TERMINAL_BEHAVIORS,
        "scenario_terminal_behavior_invalid",
    )
    outcomes = _choices(
        row["required_outcome_levels"],
        OUTCOMES,
        "scenario_outcome_levels_invalid",
        allow_empty=True,
    )
    rules = _choices(
        row["completion_rules"],
        COMPLETION_RULES,
        "scenario_completion_rules_invalid",
    )
    observation = _integer(
        row["observation_period_seconds"],
        "scenario_observation_period_invalid",
        0,
        2_592_000,
    )
    capabilities = _choices(
        row["required_capabilities"],
        AUTHORITATIVE_CAPABILITIES,
        "scenario_capabilities_invalid",
        allow_empty=True,
    )
    cancellation = _choice(
        row["cancellation_semantics"],
        CANCELLATION_SEMANTICS,
        "scenario_cancellation_semantics_invalid",
    )
    _validate_scenario_semantics(
        required_facts=required_facts,
        required_inputs=required_inputs,
        required_actions=required_actions,
        required_outcomes=outcomes,
        completion_rules=rules,
        notification_policy=notification,
        no_notification_reasons=no_notification,
        risk_level=risk,
        escalation_policy_key=escalation,
        terminal_behavior=terminal,
        observation_period_seconds=observation,
    )
    return BusinessScenarioDefinition(
        scenario_key=scenario_key,
        issue_type_aliases=aliases,
        trigger_sources=_choices(
            row["trigger_sources"],
            TRIGGERS,
            "scenario_triggers_invalid",
        ),
        required_fact_classes=required_facts,
        required_customer_inputs=required_inputs,
        risk_level=risk,
        escalation_policy_key=escalation,
        owner_queue_key=_key(
            row["owner_queue_key"],
            "scenario_owner_queue_invalid",
        ),
        required_capabilities=capabilities,
        allowed_action_classes=allowed_actions,
        required_action_classes=required_actions,
        blocked_action_classes=blocked_actions,
        notification_policy=notification,
        allowed_no_notification_reasons=no_notification,
        terminal_behavior=terminal,
        required_outcome_levels=outcomes,
        completion_rules=rules,
        definition_of_done=_label(
            row["definition_of_done"],
            "scenario_definition_of_done_invalid",
            limit=500,
        ),
        observation_period_seconds=observation,
        reopen_conditions=_choices(
            row["reopen_conditions"],
            REOPEN_CONDITIONS,
            "scenario_reopen_conditions_invalid",
            allow_empty=True,
        ),
        cancellation_semantics=cancellation,
        metrics=_choices(
            row["metrics"],
            METRICS,
            "scenario_metrics_invalid",
        ),
        scope_mode=_choice(
            row["scope_mode"],
            SCOPE_MODES,
            "scenario_scope_mode_invalid",
        ),
        lifecycle=_parse_lifecycle(row["lifecycle"]),
    )


def _validate_scenario_semantics(
    *,
    required_facts: Sequence[str],
    required_inputs: Sequence[str],
    required_actions: Sequence[str],
    required_outcomes: Sequence[str],
    completion_rules: Sequence[str],
    notification_policy: str,
    no_notification_reasons: Sequence[str],
    risk_level: str,
    escalation_policy_key: str | None,
    terminal_behavior: str,
    observation_period_seconds: int,
) -> None:
    rules = set(completion_rules)
    requirements = (
        (bool(required_facts), "required_facts_present"),
        (bool(required_inputs), "required_customer_inputs_present"),
        (bool(required_actions), "required_actions_completed"),
        (bool(required_outcomes), "required_outcomes_completed"),
        (
            notification_policy in {"required", "required_if_contactable"},
            "notification_policy_satisfied",
        ),
        (
            risk_level in {"high", "critical"},
            "no_open_high_risk_escalation",
        ),
        (
            terminal_behavior == "reclassify_only",
            "scenario_reclassified",
        ),
        (
            observation_period_seconds > 0,
            "observation_period_elapsed_if_required",
        ),
    )
    if any(required and rule not in rules for required, rule in requirements):
        raise BusinessScenarioCatalogError("scenario_completion_rules_incomplete")
    if "no_repair_required" not in rules:
        raise BusinessScenarioCatalogError("scenario_repair_rule_missing")
    if risk_level in {"high", "critical"} and escalation_policy_key is None:
        raise BusinessScenarioCatalogError("scenario_high_risk_escalation_policy_missing")
    if terminal_behavior == "reclassify_only" and "scenario_reclassify" not in required_actions:
        raise BusinessScenarioCatalogError("scenario_reclassification_action_missing")
    if terminal_behavior == "escalate_only" and not (
        {"handoff", "create_ticket"} & set(required_actions)
    ):
        raise BusinessScenarioCatalogError("scenario_escalation_action_missing")

    customer_notified_required = "customer_notified" in required_outcomes
    notify_action_required = "notify_customer" in required_actions
    if notification_policy == "required":
        if not customer_notified_required:
            raise BusinessScenarioCatalogError("scenario_required_notification_outcome_missing")
        if not notify_action_required:
            raise BusinessScenarioCatalogError("scenario_required_notification_action_missing")
    else:
        if customer_notified_required:
            raise BusinessScenarioCatalogError("scenario_conditional_notification_outcome_conflict")
        if notify_action_required:
            raise BusinessScenarioCatalogError("scenario_conditional_notification_action_conflict")

    if notification_policy == "required_if_contactable" and not no_notification_reasons:
        raise BusinessScenarioCatalogError("scenario_conditional_notification_reasons_missing")


def _parse_lifecycle(payload: Any) -> ScenarioLifecycle:
    row = _mapping(payload, "scenario_lifecycle_invalid")
    _exact_keys(row, LIFECYCLE_FIELDS, "scenario_lifecycle_fields_invalid")
    approved = _timestamp(
        row["approved_at"],
        "scenario_lifecycle_approved_at_invalid",
    )
    effective = _timestamp(
        row["effective_from"],
        "scenario_lifecycle_effective_from_invalid",
    )
    review = _timestamp(
        row["review_due"],
        "scenario_lifecycle_review_due_invalid",
    )
    expires = _optional_timestamp(
        row["expires_at"],
        "scenario_lifecycle_expires_at_invalid",
    )
    if approved > effective:
        raise BusinessScenarioCatalogError("scenario_lifecycle_approval_after_effective")
    if review <= effective:
        raise BusinessScenarioCatalogError("scenario_lifecycle_review_due_invalid")
    if expires is not None and expires <= effective:
        raise BusinessScenarioCatalogError("scenario_lifecycle_expiry_invalid")
    return ScenarioLifecycle(
        status=_choice(
            row["status"],
            LIFECYCLE_STATUSES,
            "scenario_lifecycle_status_invalid",
        ),
        owner=_label(
            row["owner"],
            "scenario_lifecycle_owner_invalid",
        ),
        approved_at=approved,
        effective_from=effective,
        review_due=review,
        expires_at=expires,
        supersedes=_optional_key(
            row["supersedes"],
            "scenario_lifecycle_supersedes_invalid",
        ),
    )


def _validate_unique_catalog(
    scenarios: Sequence[BusinessScenarioDefinition],
) -> None:
    keys: set[str] = set()
    aliases: dict[str, str] = {}
    for item in scenarios:
        if item.scenario_key in keys:
            raise BusinessScenarioCatalogError("scenario_key_duplicate")
        keys.add(item.scenario_key)
        for alias in (item.scenario_key, *item.issue_type_aliases):
            previous = aliases.get(alias)
            if previous is not None and previous != item.scenario_key:
                raise BusinessScenarioCatalogError("scenario_alias_conflict")
            aliases[alias] = item.scenario_key
    for item in scenarios:
        supersedes = item.lifecycle.supersedes
        if supersedes is not None and (
            supersedes not in keys or supersedes == item.scenario_key
        ):
            raise BusinessScenarioCatalogError("scenario_supersedes_invalid")


def _notification_satisfied(
    policy: str,
    state: str,
    allowed_reasons: Sequence[str],
) -> bool:
    value = str(state or "").strip().lower()
    if policy == "prohibited":
        return value in {"not_required", "prohibited"}
    if policy == "optional":
        return value in {"not_required", "sent", "delivered"} or _valid_notification_waiver(
            value,
            allowed_reasons,
        )
    if policy == "required_if_contactable":
        return value in {"sent", "delivered"} or _valid_notification_waiver(
            value,
            allowed_reasons,
        )
    return value in {"sent", "delivered"}


def _valid_notification_waiver(
    state: str,
    allowed_reasons: Sequence[str],
) -> bool:
    if not state.startswith("waived:"):
        return False
    reason = state.split(":", 1)[1]
    return (
        reason in NOTIFICATION_WAIVER_REASONS
        and reason in set(allowed_reasons)
    )


def _reject_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BusinessScenarioCatalogError("scenario_catalog_duplicate_json_key")
        result[key] = value
    return result


def _mapping(
    value: Any,
    reason: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise BusinessScenarioCatalogError(reason)
    return value


def _sequence(
    value: Any,
    reason: str,
) -> list[Any]:
    if not isinstance(value, list):
        raise BusinessScenarioCatalogError(reason)
    return value


def _exact_keys(
    value: Mapping[str, Any],
    expected: frozenset[str],
    reason: str,
) -> None:
    if set(value) != set(expected):
        raise BusinessScenarioCatalogError(reason)


def _label(
    value: Any,
    reason: str,
    *,
    limit: int = 160,
) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text or len(text) > limit:
        raise BusinessScenarioCatalogError(reason)
    return text


def _key(
    value: Any,
    reason: str,
) -> str:
    text = str(value or "").strip().lower()
    if not KEY_RE.fullmatch(text):
        raise BusinessScenarioCatalogError(reason)
    return text


def _optional_key(
    value: Any,
    reason: str,
) -> str | None:
    return None if value is None else _key(value, reason)


def _keys(
    value: Any,
    reason: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    rows = _sequence(value, reason)
    if (not rows and not allow_empty) or len(rows) > MAX_LIST_ITEMS:
        raise BusinessScenarioCatalogError(reason)
    result = tuple(_key(item, reason) for item in rows)
    if len(set(result)) != len(result):
        raise BusinessScenarioCatalogError(reason)
    return result


def _choice(
    value: Any,
    allowed: frozenset[str],
    reason: str,
) -> str:
    text = str(value or "").strip().lower()
    if text not in allowed:
        raise BusinessScenarioCatalogError(reason)
    return text


def _choices(
    value: Any,
    allowed: frozenset[str],
    reason: str,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    rows = _sequence(value, reason)
    if (not rows and not allow_empty) or len(rows) > MAX_LIST_ITEMS:
        raise BusinessScenarioCatalogError(reason)
    result = tuple(_choice(item, allowed, reason) for item in rows)
    if len(set(result)) != len(result):
        raise BusinessScenarioCatalogError(reason)
    return result


def _integer(
    value: Any,
    reason: str,
    minimum: int,
    maximum: int,
) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise BusinessScenarioCatalogError(reason)
    return value


def _timestamp(
    value: Any,
    reason: str,
) -> datetime:
    try:
        parsed = datetime.fromisoformat(
            str(value or "").strip().replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise BusinessScenarioCatalogError(reason) from exc
    if parsed.tzinfo is None:
        raise BusinessScenarioCatalogError(reason)
    return parsed.astimezone(timezone.utc)


def _optional_timestamp(
    value: Any,
    reason: str,
) -> datetime | None:
    return None if value is None else _timestamp(value, reason)


def _utc(
    value: datetime | None,
) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise BusinessScenarioCatalogError("scenario_evaluation_time_naive")
    return value.astimezone(timezone.utc)


def _normalized_values(
    values: Iterable[str],
) -> set[str]:
    return {
        str(value or "").strip().lower()
        for value in values
        if str(value or "").strip()
    }
