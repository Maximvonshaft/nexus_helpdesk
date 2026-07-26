#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GATES = ROOT / "config/governance/delivery-gates.v1.json"
JOURNEYS = ROOT / "config/business/golden-journeys.v1.json"
SCENARIOS = ROOT / "config/business-scenarios.v1.json"
METRICS = ROOT / "config/operations/outcome-metric-targets.v1.json"
FREEZE = ROOT / "docs/operations/90-day-scope-freeze.md"

EXPECTED_GATES = (
    "business_product",
    "architecture_data",
    "security_privacy",
    "release_runtime",
    "production_outcome",
)
EXPECTED_JOURNEYS = (
    "tracking_status_resolution",
    "delivery_delay_resolution",
    "address_contact_correction",
    "delivery_followup_work_order",
    "failed_delivery_attempt_recovery",
)
REQUIRED_JOURNEY_FIELDS = (
    "journey_key",
    "scenario_key",
    "business_owner",
    "trigger",
    "definition_of_done",
    "customer_terminal_outcomes",
    "required_authorities",
    "primary_metric_key",
    "metric_denominator",
    "recovery_path",
    "acceptance",
)


def _load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid_governance_json:{path.relative_to(ROOT)}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"governance_json_not_object:{path.relative_to(ROOT)}")
    return value


def findings() -> list[str]:
    failures: list[str] = []
    gates = _load(GATES)
    journeys = _load(JOURNEYS)
    scenarios = _load(SCENARIOS)
    metrics = _load(METRICS)

    gate_rows = gates.get("gates")
    gate_keys = tuple(
        str(row.get("gate_key") or "")
        for row in gate_rows or []
        if isinstance(row, dict)
    )
    if gates.get("schema") != "nexus.delivery-gates.v1":
        failures.append("delivery gate schema is invalid")
    if gates.get("execution_mode") != "event_driven":
        failures.append("delivery governance is not event driven")
    if gate_keys != EXPECTED_GATES:
        failures.append(
            f"delivery gate set/order mismatch: expected={EXPECTED_GATES} actual={gate_keys}"
        )
    for row in gate_rows or []:
        if not isinstance(row, dict):
            failures.append("delivery gate row is invalid")
            continue
        if not row.get("required_evidence") or not row.get("blocking_conditions"):
            failures.append(f"delivery gate lacks evidence/blockers: {row.get('gate_key')}")

    scenario_keys = {
        str(row.get("scenario_key") or "")
        for row in scenarios.get("scenarios") or []
        if isinstance(row, dict)
    }
    metric_keys = set((metrics.get("metrics") or {}).keys())
    journey_rows = journeys.get("journeys")
    journey_keys = tuple(
        str(row.get("journey_key") or "")
        for row in journey_rows or []
        if isinstance(row, dict)
    )
    if journeys.get("schema") != "nexus.golden-journeys.v1":
        failures.append("Golden Journey schema is invalid")
    if journeys.get("scope_freeze_days") != 90:
        failures.append("Golden Journey scope freeze is not 90 days")
    if journey_keys != EXPECTED_JOURNEYS:
        failures.append(
            f"Golden Journey set/order mismatch: expected={EXPECTED_JOURNEYS} actual={journey_keys}"
        )
    for row in journey_rows or []:
        if not isinstance(row, dict):
            failures.append("Golden Journey row is invalid")
            continue
        key = str(row.get("journey_key") or "")
        missing = [field for field in REQUIRED_JOURNEY_FIELDS if not row.get(field)]
        if missing:
            failures.append(f"Golden Journey missing fields: {key}:{','.join(missing)}")
        if row.get("scenario_key") not in scenario_keys:
            failures.append(f"Golden Journey scenario is not approved: {key}")
        if row.get("primary_metric_key") not in metric_keys:
            failures.append(f"Golden Journey metric is not governed: {key}")
        if len(set(row.get("customer_terminal_outcomes") or [])) < 2:
            failures.append(f"Golden Journey lacks bounded terminal outcomes: {key}")
        if len(set(row.get("acceptance") or [])) < 3:
            failures.append(f"Golden Journey lacks executable acceptance: {key}")

    allowed = set((journeys.get("scope_freeze_policy") or {}).get("allowed_change_classes") or [])
    forbidden = set((journeys.get("scope_freeze_policy") or {}).get("forbidden_change_classes") or [])
    if allowed != {
        "golden_journey_closure",
        "p0_customer_security_privacy_data_integrity",
        "duplicate_or_legacy_retirement_required_for_closure",
    }:
        failures.append("scope freeze allowed change classes drifted")
    if not {
        "new_parallel_product",
        "new_parallel_aggregate",
        "platform_abstraction_without_current_journey",
        "unbounded_compatibility_layer",
    }.issubset(forbidden):
        failures.append("scope freeze does not block speculative/parallel work")

    if not FREEZE.is_file():
        failures.append("90-day scope-freeze operating document is missing")
    else:
        source = FREEZE.read_text(encoding="utf-8")
        for marker in (
            "five Golden Journeys",
            "zero silent customer terminal outcomes",
            "A green repository gate alone does not end the freeze",
        ):
            if marker not in source:
                failures.append(f"scope-freeze marker missing: {marker}")
    return failures


def main() -> int:
    result = findings()
    print(
        json.dumps(
            {
                "schema": "nexus.value-delivery-governance-check.v1",
                "status": "pass" if not result else "fail",
                "findings": result,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if result else 0


if __name__ == "__main__":
    raise SystemExit(main())
