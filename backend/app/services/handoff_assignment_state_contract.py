from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.orm import Session

from ..enums import ConversationState, TicketStatus
from ..models import Ticket
from ..utils.time import utc_now
from ..webchat_models import WebchatConversation, WebchatHandoffRequest

_INSTALLED = False


def _accepted_handoff_candidates(session: Session) -> tuple[WebchatHandoffRequest, ...]:
    rows = tuple(session.new) + tuple(session.dirty)
    return tuple(
        row
        for row in rows
        if isinstance(row, WebchatHandoffRequest) and row.status == "accepted"
    )


def _enforce_accepted_handoff_case_state(
    session: Session,
    _flush_context,
    _instances,
) -> None:
    """Keep each accepted Ticket-backed Handoff and Case state atomic.

    Routing decides who owns the Handoff. This persistence contract does not
    select an agent or grant authority; it validates the resulting Conversation
    ownership and projects that accepted ownership onto the canonical Ticket-as-
    Case state for every Text, Voice and automatic assignment path. A genuinely
    Ticketless conversation remains outside this projection.
    """

    candidates = _accepted_handoff_candidates(session)
    if not candidates:
        return

    with session.no_autoflush:
        for request_row in candidates:
            if request_row.id is None:
                raise RuntimeError("accepted_handoff_must_be_persisted")
            agent_id = request_row.assigned_agent_id
            if agent_id is None:
                raise RuntimeError("accepted_handoff_missing_agent")

            conversation = session.get(
                WebchatConversation,
                int(request_row.conversation_id),
            )
            if conversation is None:
                raise RuntimeError("accepted_handoff_conversation_missing")

            request_ticket_id = request_row.ticket_id
            conversation_ticket_id = conversation.ticket_id
            if request_ticket_id is None and conversation_ticket_id is None:
                continue
            if (
                request_ticket_id is None
                or conversation_ticket_id is None
                or int(request_ticket_id) != int(conversation_ticket_id)
            ):
                raise RuntimeError("accepted_handoff_ticket_relationship_conflict")

            if (
                conversation.current_handoff_request_id != request_row.id
                or conversation.handoff_status != "accepted"
                or conversation.active_agent_id != agent_id
                or not conversation.ai_suspended
            ):
                raise RuntimeError(
                    "accepted_handoff_conversation_projection_invalid"
                )

            ticket = session.get(Ticket, int(request_ticket_id))
            if ticket is None:
                raise RuntimeError("accepted_handoff_ticket_missing")

            ticket.assignee_id = int(agent_id)
            ticket.status = TicketStatus.in_progress
            ticket.conversation_state = ConversationState.human_owned
            ticket.required_action = None
            ticket.updated_at = utc_now()


def install_handoff_assignment_state_contract() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    event.listen(
        Session,
        "before_flush",
        _enforce_accepted_handoff_case_state,
    )
    _INSTALLED = True


__all__ = ["install_handoff_assignment_state_contract"]
