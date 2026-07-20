from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_canonical_ticket_authority_enforces_safe_closure_and_reopen_invalidation() -> None:
    source = (ROOT / "backend/app/services/ticket_service.py").read_text(encoding="utf-8")
    assert "require_closure_ready(db, ticket)" in source
    assert "append_closure_receipt_event(" in source
    assert "invalidate_latest_closure_receipt(" in source
    assert "payload.new_status == TicketStatus.closed" in source


def test_closure_receipt_is_derived_from_durable_sources_and_contains_no_payloads() -> None:
    source = (ROOT / "backend/app/services/ticket_closure_readiness.py").read_text(encoding="utf-8")
    for marker in (
        "nexus.ticket-closure-evidence.v1",
        "nexus.ticket-closure-receipt.v1",
        "TicketEvent",
        "TicketOutboundMessage",
        "BackgroundJob",
        "evaluate_scenario_readiness(",
        '"contains_payloads": False',
        "receipt_sha256",
    ):
        assert marker in source


def test_ticket_close_cannot_fall_back_to_resolution_category_only() -> None:
    canonical = (ROOT / "backend/app/services/ticket_service.py").read_text(encoding="utf-8")
    core = (ROOT / "backend/app/services/ticket_service_core.py").read_text(encoding="utf-8")
    assert "require_closure_ready" in canonical
    assert "Resolution category is required before closing a ticket" in core
