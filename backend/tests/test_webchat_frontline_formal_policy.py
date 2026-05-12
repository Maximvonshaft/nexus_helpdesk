from __future__ import annotations

from types import SimpleNamespace

from app.enums import TicketStatus
from app.services.webchat_formal_policy import (
    is_formal_resolution_context,
    webchat_formal_outbound_enabled,
    webchat_frontline_ai_enabled,
)


def test_webchat_frontline_ai_enabled_by_default(monkeypatch):
    monkeypatch.delenv("WEBCHAT_FRONTLINE_AI_ENABLED", raising=False)
    assert webchat_frontline_ai_enabled() is True


def test_webchat_formal_outbound_disabled_by_default(monkeypatch):
    monkeypatch.delenv("WEBCHAT_FORMAL_OUTBOUND_ENABLED", raising=False)
    assert webchat_formal_outbound_enabled() is False


def test_webchat_formal_resolution_context_from_status():
    ticket = SimpleNamespace(status=TicketStatus.resolved, resolution_summary=None, customer_update=None)
    assert is_formal_resolution_context(ticket, source="webchat_ai_reply") is True


def test_webchat_formal_resolution_context_from_human_fields():
    ticket = SimpleNamespace(status=TicketStatus.in_progress, resolution_summary="Re-deliver tomorrow", customer_update=None)
    assert is_formal_resolution_context(ticket, source="webchat_ai_reply") is True

    ticket = SimpleNamespace(status=TicketStatus.in_progress, resolution_summary=None, customer_update="Customer should be notified")
    assert is_formal_resolution_context(ticket, source="webchat_ai_reply") is True


def test_webchat_frontline_context_allowed_without_human_resolution():
    ticket = SimpleNamespace(status=TicketStatus.in_progress, resolution_summary=None, customer_update=None)
    assert is_formal_resolution_context(ticket, source="webchat_ai_reply") is False


def test_webchat_formal_resolution_source_blocks_even_before_resolved():
    ticket = SimpleNamespace(status=TicketStatus.in_progress, resolution_summary=None, customer_update=None)
    assert is_formal_resolution_context(ticket, source="auto_reply_from_resolution") is True
