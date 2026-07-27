from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_canonical_ticket_authority_enforces_safe_closure_and_reopen_invalidation() -> None:
    source = (ROOT / "backend/app/services/ticket_service.py").read_text(encoding="utf-8")
    assert "require_closure_ready(db, ticket)" in source
    assert "append_closure_receipt_event(" in source
    assert "invalidate_latest_closure_receipt(" in source
    assert "payload.new_status == TicketStatus.closed" in source


def test_closure_receipt_uses_structured_case_ledgers_and_contains_no_payloads() -> None:
    source = (ROOT / "backend/app/services/ticket_closure_readiness.py").read_text(encoding="utf-8")
    for marker in (
        "nexus.case-closure-evidence.v2",
        "nexus.ticket-closure-receipt.v3",
        "scenario_assignment_revision",
        "scenario_catalog_sha256",
        "scenario_definition_sha256",
        "project_case_ledger(",
        "record_case_evidence(",
        "append_case_outcome(",
        "CaseOutcomeRecord",
        "TicketOutboundMessage",
        "evaluate_scenario_readiness(",
        '"contains_payloads": False',
        "receipt_sha256",
    ):
        assert marker in source

    for retired in (
        "_parse_event_payload(",
        "_latest_explicit_evidence(",
        "_event_action_projection(",
        "_explicit_projection(",
        "background_job_ids",
        "ticket_event_ids",
    ):
        assert retired not in source


def test_ticket_event_is_timeline_projection_not_business_completion_authority() -> None:
    source = (ROOT / "backend/app/services/ticket_closure_readiness.py").read_text(encoding="utf-8")
    assert "nexus.case-ledger-timeline-projection.v1" in source
    assert "db.query(TicketEvent)" not in source


def test_ticket_close_cannot_fall_back_to_resolution_category_only() -> None:
    canonical = (ROOT / "backend/app/services/ticket_service.py").read_text(encoding="utf-8")
    core = (ROOT / "backend/app/services/ticket_service_core.py").read_text(encoding="utf-8")
    assert "require_closure_ready" in canonical
    assert "Resolution category is required before closing a ticket" in core
