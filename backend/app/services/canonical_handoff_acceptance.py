from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..models import Ticket, User
from ..webchat_models import WebchatConversation, WebchatHandoffRequest
from . import agent_routing_service
from . import webchat_handoff_service


def accept_handoff_request(
    db: Session,
    *,
    request_id: int,
    current_user: User,
    note: str | None = None,
) -> dict:
    """Accept one Handoff through the same authority as automatic and Voice.

    Ticketless conversations retain their established scope-grant authority.
    Every Ticket-backed manual acceptance revalidates the immutable Routing Plan,
    exact Scenario Queue, required capabilities, presence, heartbeat and capacity
    at the acceptance transaction boundary.
    """

    query = db.query(WebchatHandoffRequest).filter(
        WebchatHandoffRequest.id == int(request_id)
    )
    if db.bind and db.bind.dialect.name.startswith("postgresql"):
        query = query.with_for_update()
    request_row = query.first()
    if request_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="webchat handoff request not found",
        )
    if request_row.ticket_id is None:
        return webchat_handoff_service.accept_handoff_request(
            db,
            request_id=request_id,
            current_user=current_user,
            note=note,
        )

    conversation = db.get(WebchatConversation, request_row.conversation_id)
    ticket = db.get(Ticket, request_row.ticket_id)
    if conversation is None or ticket is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="webchat handoff source is missing",
        )
    if (
        request_row.status == "accepted"
        and request_row.assigned_agent_id == current_user.id
    ):
        return webchat_handoff_service.serialize_handoff_request(
            db,
            request_row,
            current_user=current_user,
            conversation=conversation,
            ticket=ticket,
        )
    if request_row.status != "requested":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="webchat handoff request is not open",
        )

    result = agent_routing_service.assign_handoff_to_agent(
        db,
        request_row=request_row,
        conversation=conversation,
        user=current_user,
        mode="manual",
    )
    if note:
        request_row.decision_note = " ".join(str(note).strip().split())[:1000] or None
    db.flush()
    # The canonical assignment result is already the public Handoff projection.
    return result


__all__ = ["accept_handoff_request"]
