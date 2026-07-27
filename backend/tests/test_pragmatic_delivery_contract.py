from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GOLDEN_PATH = ROOT / "config/product/golden-journeys.v1.json"
GATES_PATH = ROOT / "config/governance/delivery-gates.v1.json"
SCENARIOS_PATH = ROOT / "backend/app/config/business_scenarios.v1.json"
FREEZE_PATH = ROOT / "docs/product/90-day-value-closure.md"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_exactly_five_golden_journeys_select_one_scenario_authority():
    portfolio = load(GOLDEN_PATH)
    catalog = load(SCENARIOS_PATH)

    assert portfolio["schema"] == "nexus.golden-journeys.v2"
    assert portfolio["scenario_authority"] == {
        "path": "backend/app/config/business_scenarios.v1.json",
        "schema": "nexus.business-scenario-catalog.v1",
        "runtime_loader": (
            "app.services.nexus_osr.business_scenarios."
            "load_business_scenario_catalog"
        ),
        "safe_closure": "app.services.ticket_closure_readiness",
    }
    assert (
        portfolio["aggregate_authority"]
        == "config/architecture/business-aggregate-authority.v1.json"
    )

    selected = portfolio["selected_scenarios"]
    assert len(selected) == 5
    assert [row["launch_order"] for row in selected] == [1, 2, 3, 4, 5]
    assert len({row["scenario_key"] for row in selected}) == 5
    assert all(
        set(row) == {"scenario_key", "launch_order", "business_owner"}
        for row in selected
    )

    approved = {
        item["scenario_key"]: item
        for item in catalog["scenarios"]
        if item.get("lifecycle", {}).get("status") == "approved"
    }
    for row in selected:
        scenario = approved[row["scenario_key"]]
        assert row["business_owner"] == scenario["lifecycle"]["owner"]

    forbidden = set(portfolio["forbidden_duplicate_fields"])
    assert forbidden
    assert all(not (set(row) & forbidden) for row in selected)
    serialized_rows = json.dumps(selected, ensure_ascii=False)
    for field in forbidden:
        assert f'"{field}"' not in serialized_rows


def test_governance_is_five_event_driven_gates_not_a_scheduled_lane_system():
    contract = load(GATES_PATH)

    assert contract["schema"] == "nexus.delivery-gates.v1"
    assert contract["execution_mode"] == "event_driven"
    assert len(contract["gates"]) == 5
    assert [gate["gate_key"] for gate in contract["gates"]] == [
        "business_product",
        "architecture_data",
        "security_privacy",
        "release_runtime",
        "production_outcome",
    ]
    assert contract["trigger_events"]

    rendered = json.dumps(contract, ensure_ascii=False).lower()
    for forbidden in (
        "hourly",
        "cron",
        "every_hour",
        "15_lane",
        "fifteen_lane",
    ):
        assert forbidden not in rendered

    for gate in contract["gates"]:
        assert gate["question"].strip()
        assert gate["required_evidence"]
        assert gate["blocking_conditions"]


def test_scope_freeze_links_one_scenario_authority_and_forbids_parallel_products():
    text = FREEZE_PATH.read_text(encoding="utf-8")

    assert "config/product/golden-journeys.v1.json" in text
    assert "backend/app/config/business_scenarios.v1.json" in text
    assert "selection and ordering only" in text
    assert "config/governance/delivery-gates.v1.json" in text
    assert (
        "second Case, Conversation, Handoff, Queue, SLA, Privacy, Metrics or Release authority"
        in text
    )
    assert "zero silent terminal customer outcomes" in text
