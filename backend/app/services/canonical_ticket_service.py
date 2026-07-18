"""Canonical public facade for ticket-domain operations.

The private implementation is capability-native and contains the only ticket
business logic. This module exposes that authority without import-time mutation.
"""

from . import ticket_service_core as _core
from .ticket_service_core import *  # noqa: F401,F403


def __getattr__(name: str):
    return getattr(_core, name)


__all__ = [
    "add_ai_intake",
    "add_attachment",
    "add_comment",
    "add_internal_note",
    "assign_ticket",
    "change_status",
    "create_ticket",
    "escalate_ticket",
    "get_customer_history",
    "get_ticket_events",
    "get_ticket_or_404",
    "get_ticket_stats",
    "list_tickets",
    "reopen_ticket",
    "save_outbound_draft",
    "send_outbound_message",
    "update_ticket",
    "validate_assignee_team",
]
