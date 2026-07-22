from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_customer_visible_message_has_one_physical_persistence_authority() -> None:
    authority = _source(
        "backend/app/services/customer_visible_message_service.py"
    )
    assert "ticket: Ticket | None" in authority
    assert "WebchatMessage(" in authority
    assert 'event_type="handoff.agent_reply_sent"' in authority

    for relative in (
        "backend/app/services/webchat_ai_service.py",
        "backend/app/services/conversation_operator_service.py",
        "backend/app/services/webchat_service.py",
    ):
        source = _source(relative)
        assert "create_customer_visible_message" in source
        assert "WebchatMessage(" not in source
        assert "TicketOutboundMessage(" not in source
        assert "TicketComment(" not in source


def test_customer_visible_message_authority_is_declared() -> None:
    manifest = _source("config/architecture/service-authority.v1.json")
    inventory = _source(
        "docs/ai/codebase-rationalization-inventory.v2.yaml"
    )
    path = "backend/app/services/customer_visible_message_service.py"
    assert '"responsibility": "customer-visible-message-persistence"' in manifest
    assert path in manifest
    assert "customer_visible_message_persistence" in inventory
    assert path in inventory
