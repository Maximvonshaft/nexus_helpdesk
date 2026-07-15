"""Capability-bound facade for the retained Lite case state machine."""

from . import lite_service as _legacy
from .canonical_ticket_service import (
    add_ai_intake,
    add_internal_note,
    change_status,
    create_ticket,
    get_ticket_or_404,
    list_tickets,
    validate_assignee_team,
)

_legacy.add_ai_intake = add_ai_intake
_legacy.add_internal_note = add_internal_note
_legacy.change_status = change_status
_legacy.create_ticket = create_ticket
_legacy.get_ticket_or_404 = get_ticket_or_404
_legacy.list_tickets = list_tickets
_legacy.validate_assignee_team = validate_assignee_team

from .lite_service import (  # noqa: E402
    LITE_STATUS_ORDER,
    assign_lite_case,
    change_lite_status,
    create_lite_case,
    get_lite_case,
    list_lite_cases,
    save_ai_intake_lite,
    save_human_note_lite,
    update_lite_case,
    workflow_update_lite_case,
)

__all__ = [
    "LITE_STATUS_ORDER",
    "assign_lite_case",
    "change_lite_status",
    "create_lite_case",
    "get_lite_case",
    "list_lite_cases",
    "save_ai_intake_lite",
    "save_human_note_lite",
    "update_lite_case",
    "workflow_update_lite_case",
]
