from fastapi import HTTPException, status

from ..enums import TicketStatus

ALLOWED_TRANSITIONS = {
    TicketStatus.new: {TicketStatus.pending_assignment, TicketStatus.in_progress, TicketStatus.canceled},
    TicketStatus.pending_assignment: {TicketStatus.in_progress, TicketStatus.canceled, TicketStatus.escalated},
    TicketStatus.in_progress: {TicketStatus.waiting_customer, TicketStatus.waiting_internal, TicketStatus.resolved, TicketStatus.canceled, TicketStatus.escalated},
    TicketStatus.waiting_customer: {TicketStatus.in_progress, TicketStatus.resolved, TicketStatus.canceled, TicketStatus.escalated},
    TicketStatus.waiting_internal: {TicketStatus.in_progress, TicketStatus.resolved, TicketStatus.canceled, TicketStatus.escalated},
    TicketStatus.escalated: {TicketStatus.in_progress, TicketStatus.waiting_internal, TicketStatus.resolved, TicketStatus.canceled},
    TicketStatus.resolved: {TicketStatus.closed},
    TicketStatus.closed: set(),
    TicketStatus.canceled: set(),
}

REQUIRES_NOTE = {TicketStatus.canceled, TicketStatus.closed}
TERMINAL_STATUSES = {TicketStatus.closed, TicketStatus.canceled}
PAUSE_CANDIDATES = {TicketStatus.waiting_customer, TicketStatus.waiting_internal}


def validate_transition(current_status: TicketStatus, new_status: TicketStatus):
    if new_status not in ALLOWED_TRANSITIONS.get(current_status, set()):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid transition: {current_status.value} -> {new_status.value}",
        )


def requires_note(new_status: TicketStatus) -> bool:
    return new_status in REQUIRES_NOTE


def is_terminal(status_value: TicketStatus) -> bool:
    return status_value in TERMINAL_STATUSES
