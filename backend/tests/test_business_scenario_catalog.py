from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.services.nexus_osr.business_scenarios import (
    AUTHORITATIVE_CAPABILITIES,
    BusinessScenarioCatalogError,
    evaluate_scenario_readiness,
    load_business_scenario_catalog,
    parse_business_scenario_catalog,
    resolve_business_scenario,
)

ROOT = Path(__file__).resolve().parents[2]
CATALOG_PATH = ROOT / "backend" / "app" / "config" / "business_scenarios.v1.json"
VALIDATOR_PATH = ROOT / "backend" / "scripts" / "validate_business_scenario_catalog.py"
CATALOG_AT = datetime(2026, 7, 12, tzinfo=timezone.utc)
EXPECTED_KEYS = {
    "tracking_status_inquiry",
    "delivery_eta_delay_inquiry",
    "address_contact_correction",
    "delivery_followup_work_order",
    "failed_repeated_delivery_attempt",
    "formal_complaint",
    "refund_compensation_request",
    "legal_privacy_high_risk",
    "return_refusal_flow",
    "cod_payment_anomaly",
    "operations_dispatch_failure",
    "missing_information_intake",
}


def _payload() -> dict:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def _parse(payload: dict):
    return parse_business_scenario_catalog(payload, source_sha256="fixture")


def _load():
    """Load the checked-in catalog against a fixed acceptance instant."""

    return load_business_scenario_catalog(CATALOG_PATH, at=CATALOG_AT)


def _scenario(payload: dict, key: str) -> dict:
    return next(item for item in payload["scenarios"] if item["scenario_key"] == key)


def test_production_catalog_is_active_bounded_and_complete() -> None:
    catalog = _load()
    assert catalog.schema == "nexus.business-scenario-catalog.v1"
    assert {item.scenario_key for item in catalog.scenarios} == EXPECTED_KEYS
    assert len(catalog.scenarios) == 12
    assert len(catalog.source_sha256) == 64
    assert all(item.scope_mode == "inherit_resolved_scope" for item in catalog.scenarios)
    assert all(item.lifecycle.status == "approved" for item in catalog.scenarios)


def test_safe_summary_contains_no_scenario_body_or_customer_data() -> None:
    summary = _load().safe_summary()
    assert summary["scenario_count"] == 12
    assert set(summary["scenario_keys"]) == EXPECTED_KEYS
    rendered = json.dumps(summary, sort_keys=True)
    assert "definition_of_done" not in rendered
    assert "tracking_reference" not in rendered


def test_exact_alias_resolution_is_deterministic_and_no_fuzzy_match_exists() -> None:
    catalog = _load()
    assert resolve_business_scenario(catalog, issue_type="parcel_status").scenario_key == "tracking_status_inquiry"
    assert resolve_business_scenario(catalog, scenario_key="cod_payment_anomaly").scenario_key == "cod_payment_anomaly"
    with pytest.raises(BusinessScenarioCatalogError, match="scenario_not_found"):
        resolve_business_scenario(catalog, issue_type="parcel_stat")
    with pytest.raises(BusinessScenarioCatalogError, match="scenario_identity_conflict"):
        resolve_business_scenario(
            catalog,
            scenario_key="tracking_status_inquiry",
            issue_type="refund_request",
        )


@pytest.mark.parametrize("fact_class", ["customer_claim", "prior_ai_output", "ai_recommendation"])
def test_non_authoritative_evidence_cannot_be_required_fact(fact_class: str) -> None:
    payload = _payload()
    payload["scenarios"][0]["required_fact_classes"] = [fact_class]
    with pytest.raises(BusinessScenarioCatalogError, match="scenario_fact_authority_forbidden"):
        _parse(payload)


def test_duplicate_json_key_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text(
        '{"schema":"nexus.business-scenario-catalog.v1","schema":"duplicate"}',
        encoding="utf-8",
    )
    with pytest.raises(BusinessScenarioCatalogError, match="scenario_catalog_duplicate_json_key"):
        load_business_scenario_catalog(path, at=CATALOG_AT)


def test_alias_conflict_and_repeated_key_fail_closed() -> None:
    payload = _payload()
    payload["scenarios"][1]["issue_type_aliases"][0] = "parcel_status"
    with pytest.raises(BusinessScenarioCatalogError, match="scenario_alias_conflict"):
        _parse(payload)

    payload = _payload()
    payload["scenarios"][0]["issue_type_aliases"][0] = payload["scenarios"][0]["scenario_key"]
    with pytest.raises(BusinessScenarioCatalogError, match="scenario_alias_repeats_key"):
        _parse(payload)


def test_review_due_fails_active_loading(tmp_path: Path) -> None:
    payload = _payload()
    for row in payload["scenarios"]:
        row["lifecycle"]["review_due"] = "2026-07-12T00:00:00Z"
    path = tmp_path / "stale.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(BusinessScenarioCatalogError, match="scenario_catalog_contains_inactive_definition"):
        load_business_scenario_catalog(
            path,
            at=datetime(2026, 7, 13, tzinfo=timezone.utc),
        )


def test_semantic_rules_and_high_risk_escalation_are_mandatory() -> None:
    payload = _payload()
    payload["scenarios"][0]["completion_rules"].remove("required_outcomes_completed")
    with pytest.raises(BusinessScenarioCatalogError, match="scenario_completion_rules_incomplete"):
        _parse(payload)

    payload = _payload()
    _scenario(payload, "formal_complaint")["escalation_policy_key"] = None
    with pytest.raises(BusinessScenarioCatalogError, match="scenario_high_risk_escalation_policy_missing"):
        _parse(payload)


def test_required_action_must_be_allowed_and_allowed_action_cannot_be_blocked() -> None:
    payload = _payload()
    payload["scenarios"][0]["required_action_classes"].append("return_process")
    with pytest.raises(BusinessScenarioCatalogError, match="scenario_required_action_not_allowed"):
        _parse(payload)

    payload = _payload()
    payload["scenarios"][0]["blocked_action_classes"].append("tracking_lookup")
    with pytest.raises(BusinessScenarioCatalogError, match="scenario_action_conflict"):
        _parse(payload)


def test_required_notification_has_no_silent_bypass() -> None:
    payload = _payload()
    payload["scenarios"][0]["allowed_no_notification_reasons"] = ["no_contact_method"]
    with pytest.raises(BusinessScenarioCatalogError, match="scenario_required_notification_has_bypass"):
        _parse(payload)


def test_required_capabilities_use_authoritative_permission_vocabulary() -> None:
    catalog = _load()
    for item in catalog.scenarios:
        assert set(item.required_capabilities).issubset(AUTHORITATIVE_CAPABILITIES)
    address = resolve_business_scenario(catalog, scenario_key="address_contact_correction")
    work_order = resolve_business_scenario(catalog, scenario_key="delivery_followup_work_order")
    assert "tool:speedaf.order.update_address:write" in address.required_capabilities
    assert "tool:speedaf.work_order.create:write" in work_order.required_capabilities


def test_unknown_capability_fails_closed() -> None:
    payload = _payload()
    payload["scenarios"][0]["required_capabilities"].append("speedaf.address_update.write")
    with pytest.raises(BusinessScenarioCatalogError, match="scenario_capabilities_invalid"):
        _parse(payload)


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("allowed_no_notification_reasons", ["uncontrolled_reason"], "scenario_no_notification_reasons_invalid"),
        ("cancellation_semantics", "invented_cancel_rule", "scenario_cancellation_semantics_invalid"),
    ],
)
def test_reason_code_vocabularies_fail_closed(field: str, value, reason: str) -> None:
    payload = _payload()
    row = _scenario(payload, "return_refusal_flow")
    row[field] = value
    with pytest.raises(BusinessScenarioCatalogError, match=reason):
        _parse(payload)


def test_conditional_notification_cannot_require_unconditional_action_or_outcome() -> None:
    payload = _payload()
    row = _scenario(payload, "return_refusal_flow")
    row["required_outcome_levels"].append("customer_notified")
    with pytest.raises(BusinessScenarioCatalogError, match="scenario_conditional_notification_outcome_conflict"):
        _parse(payload)

    payload = _payload()
    row = _scenario(payload, "return_refusal_flow")
    row["required_action_classes"].append("notify_customer")
    with pytest.raises(BusinessScenarioCatalogError, match="scenario_conditional_notification_action_conflict"):
        _parse(payload)


def test_tracking_readiness_requires_fact_action_notification_and_business_result() -> None:
    scenario = resolve_business_scenario(
        _load(),
        scenario_key="tracking_status_inquiry",
    )
    blocked = evaluate_scenario_readiness(
        scenario,
        available_fact_classes=["parcel_identity"],
        available_customer_inputs=["tracking_reference"],
        completed_action_classes=["tracking_lookup"],
        completed_outcome_levels=["customer_notified"],
        customer_notification_state="sent",
    )
    assert blocked.closure_ready is False
    assert blocked.missing_fact_classes == ("tracking_current_status",)
    assert blocked.missing_action_classes == ("notify_customer",)
    assert blocked.missing_outcome_levels == ("business_result_confirmed",)

    ready = evaluate_scenario_readiness(
        scenario,
        available_fact_classes=["parcel_identity", "tracking_current_status"],
        available_customer_inputs=["tracking_reference"],
        completed_action_classes=["tracking_lookup", "notify_customer"],
        completed_outcome_levels=["customer_notified", "business_result_confirmed"],
        customer_notification_state="delivered",
    )
    assert ready.closure_ready is True
    assert ready.blocked_reasons == ()


def test_valid_conditional_notification_waiver_satisfies_policy_without_false_outcome() -> None:
    scenario = resolve_business_scenario(
        _load(),
        scenario_key="return_refusal_flow",
    )
    result = evaluate_scenario_readiness(
        scenario,
        available_fact_classes=scenario.required_fact_classes,
        available_customer_inputs=scenario.required_customer_inputs,
        completed_action_classes=scenario.required_action_classes,
        completed_outcome_levels=scenario.required_outcome_levels,
        customer_notification_state="waived:no_contact_method",
        observation_period_elapsed=True,
    )
    assert result.notification_satisfied is True
    assert "customer_notified" not in scenario.required_outcome_levels
    assert "notify_customer" not in scenario.required_action_classes
    assert result.closure_ready is True


def test_invalid_or_unapproved_notification_waiver_is_rejected() -> None:
    scenario = resolve_business_scenario(
        _load(),
        scenario_key="return_refusal_flow",
    )
    kwargs = dict(
        available_fact_classes=scenario.required_fact_classes,
        available_customer_inputs=scenario.required_customer_inputs,
        completed_action_classes=scenario.required_action_classes,
        completed_outcome_levels=scenario.required_outcome_levels,
        observation_period_elapsed=True,
    )
    assert evaluate_scenario_readiness(
        scenario,
        **kwargs,
        customer_notification_state="waived",
    ).notification_satisfied is False
    assert evaluate_scenario_readiness(
        scenario,
        **kwargs,
        customer_notification_state="waived:legal_hold",
    ).notification_satisfied is False


def test_observation_repair_and_high_risk_state_block_closure() -> None:
    scenario = resolve_business_scenario(
        _load(),
        scenario_key="failed_repeated_delivery_attempt",
    )
    kwargs = {
        "available_fact_classes": scenario.required_fact_classes,
        "available_customer_inputs": scenario.required_customer_inputs,
        "completed_action_classes": scenario.required_action_classes,
        "completed_outcome_levels": scenario.required_outcome_levels,
        "customer_notification_state": "waived:no_contact_method",
    }
    waiting = evaluate_scenario_readiness(scenario, **kwargs)
    assert "observation_period_not_elapsed" in waiting.blocked_reasons

    repairing = evaluate_scenario_readiness(
        scenario,
        **kwargs,
        observation_period_elapsed=True,
        repair_required=True,
        open_high_risk_escalation=True,
    )
    assert set(repairing.blocked_reasons) >= {
        "repair_required",
        "high_risk_escalation_open",
    }

    assert evaluate_scenario_readiness(
        scenario,
        **kwargs,
        observation_period_elapsed=True,
    ).closure_ready is True


def test_escalate_and_reclassify_scenarios_cannot_be_falsely_closed() -> None:
    catalog = _load()
    legal = resolve_business_scenario(catalog, scenario_key="legal_privacy_high_risk")
    intake = resolve_business_scenario(catalog, scenario_key="missing_information_intake")

    legal_result = evaluate_scenario_readiness(
        legal,
        available_fact_classes=legal.required_fact_classes,
        available_customer_inputs=legal.required_customer_inputs,
        completed_action_classes=legal.required_action_classes,
        completed_outcome_levels=legal.required_outcome_levels,
        customer_notification_state="waived:legal_hold",
    )
    assert "terminal_behavior_escalate_only" in legal_result.blocked_reasons

    intake_result = evaluate_scenario_readiness(
        intake,
        available_customer_inputs=intake.required_customer_inputs,
        completed_action_classes=intake.required_action_classes,
        completed_outcome_levels=intake.required_outcome_levels,
        customer_notification_state="waived:no_contact_method",
    )
    assert intake_result.notification_satisfied is True
    assert "terminal_behavior_reclassify_only" in intake_result.blocked_reasons


def test_validator_cli_emits_only_bounded_safe_summary() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(VALIDATOR_PATH),
            "--path",
            str(CATALOG_PATH),
            "--at",
            "2026-07-12T00:00:00Z",
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["catalog"]["scenario_count"] == 12
    assert "definition_of_done" not in completed.stdout.lower()
    assert "required_customer_inputs" not in completed.stdout.lower()


def test_validator_cli_returns_bounded_reason_for_invalid_catalog(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{}", encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(VALIDATOR_PATH), "--path", str(invalid)],
        check=False,
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert completed.returncode == 2
    assert json.loads(completed.stdout) == {
        "ok": False,
        "reason": "scenario_catalog_fields_invalid",
        "schema": "nexus.business-scenario-validation.v1",
    }
