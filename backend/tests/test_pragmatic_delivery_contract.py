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


def test_exactly_five_golden_journeys_use_approved_scenarios_and_authorities():
    golden = load(GOLDEN_PATH)
    catalog = load(SCENARIOS_PATH)

    assert golden["schema"] == "nexus.golden-journeys.v1"
    journeys = golden["journeys"]
    assert len(journeys) == 5
    assert len({item["journey_key"] for item in journeys}) == 5
    assert len({item["scenario_key"] for item in journeys}) == 5

    approved_scenarios = {
        item["scenario_key"]
        for item in catalog["scenarios"]
        if item.get("lifecycle", {}).get("status") == "approved"
    }
    assert {item["scenario_key"] for item in journeys}.issubset(
        approved_scenarios
    )

    assert golden["authority"] == {
        "live_interaction": "WebchatConversation",
        "durable_case": "Ticket",
        "handoff": "WebchatHandoffRequest",
        "queue_projection": "OperatorTask",
        "evidence": "CaseEvidenceRecord",
        "outcome": "CaseOutcomeRecord",
        "safe_closure": "ticket_closure_readiness",
    }
    for journey in journeys:
        assert journey["definition_of_done"].strip()
        assert journey["terminal_customer_outcomes"]
        assert journey["primary_metrics"]
        assert journey["failure_injections"]
        assert "Ticket-as-Case" in journey["case_creation_rule"]


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


def test_scope_freeze_links_the_same_authorities_and_forbids_parallel_products():
    text = FREEZE_PATH.read_text(encoding="utf-8")

    assert "config/product/golden-journeys.v1.json" in text
    assert "config/governance/delivery-gates.v1.json" in text
    assert "second Case, Conversation, Handoff, Queue, SLA, Privacy, Metrics or Release authority" in text
    assert "zero silent terminal customer outcomes" in text
