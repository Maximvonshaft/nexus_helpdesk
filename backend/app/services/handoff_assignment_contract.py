from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..models import User
from .permissions import (
    CAP_WEBCHAT_HANDOFF_ACCEPT,
    resolve_capabilities,
)

_INSTALLED = False


def _eligible_voice_agents(
    db: Session,
    *,
    request_row,
    control,
):
    """Return Voice candidates not attempted in the current generation.

    Historical offers belong to their prior generation and do not permanently
    remove an otherwise eligible agent from bounded recovery.
    """

    from . import agent_routing_service as routing

    if not control.country_code:
        return []
    plan = routing.ensure_handoff_routing_plan(db, request_row=request_row)
    candidates = routing.eligible_agents(
        db,
        plan=plan,
        control=control,
        channel_kind="voice",
        require_voice=True,
    )
    result = []
    for user, state in candidates:
        if not routing._candidate_authorized(
            db,
            plan=plan,
            control=control,
            user=user,
            channel_kind="voice",
        ):
            continue
        occupied = routing._core.active_voice_load(db, user_id=user.id)
        reserved = routing._core.reserved_voice_offer_count(db, user_id=user.id)
        if occupied + reserved >= state.max_concurrent_voice_calls:
            continue
        result.append((user, state))
    return result


def _ticketless_declined(
    db: Session,
    *,
    request_id: int,
    user_id: int,
) -> bool:
    from . import agent_routing_service as routing

    return bool(
        db.query(routing._core.WebchatHandoffDecision.id)
        .filter(
            routing._core.WebchatHandoffDecision.request_id == request_id,
            routing._core.WebchatHandoffDecision.actor_id == user_id,
            routing._core.WebchatHandoffDecision.decision == "declined",
        )
        .first()
    )


def _eligible_text_request_for_agent(
    db: Session,
    *,
    user: User,
):
    """Use generation attempts for Ticket-backed routing, legacy decision for Ticketless."""

    from . import agent_routing_service as routing

    voice_exists = (
        db.query(routing._core.WebchatVoiceSession.id)
        .filter(
            routing._core.WebchatVoiceSession.conversation_id
            == routing._core.WebchatHandoffRequest.conversation_id,
            routing._core.WebchatVoiceSession.status.in_(
                sorted(routing._core.VOICE_OPEN_SESSION_STATUSES)
            ),
        )
        .exists()
    )
    query = (
        db.query(
            routing._core.WebchatHandoffRequest,
            routing._core.WebchatConversation,
            routing._core.ConversationControl,
        )
        .join(
            routing._core.WebchatConversation,
            routing._core.WebchatConversation.id
            == routing._core.WebchatHandoffRequest.conversation_id,
        )
        .join(
            routing._core.ConversationControl,
            routing._core.ConversationControl.conversation_id
            == routing._core.WebchatConversation.id,
        )
        .filter(
            routing._core.WebchatHandoffRequest.status == "requested",
            routing._core.WebchatConversation.status == "open",
            routing._core.ConversationControl.country_code.is_not(None),
            ~voice_exists,
        )
        .order_by(
            routing._core.WebchatHandoffRequest.requested_at.asc(),
            routing._core.WebchatHandoffRequest.id.asc(),
        )
        .limit(100)
    )
    for request_row, conversation, control in routing._core._lock(query, db).all():
        plan = routing.ensure_handoff_routing_plan(db, request_row=request_row)
        if plan is None and _ticketless_declined(
            db,
            request_id=request_row.id,
            user_id=user.id,
        ):
            continue
        if routing._candidate_authorized(
            db,
            plan=plan,
            control=control,
            user=user,
            channel_kind="text",
        ):
            return request_row, conversation, control
    return None


def _accept_ticket_handoff(
    db: Session,
    *,
    request_id: int,
    current_user: User,
    note: str | None = None,
) -> dict[str, Any]:
    """Accept a Ticket-backed Handoff through the canonical routing authority."""

    from . import agent_routing_service as routing
    from . import webchat_handoff_service_core as core

    capabilities = resolve_capabilities(current_user, db)
    if CAP_WEBCHAT_HANDOFF_ACCEPT not in capabilities:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="webchat_handoff_accept_requires_capability",
        )

    row = core._request_by_id(db, request_id, lock=True)
    conversation, ticket = core._load_conversation_ticket(db, row)
    core._ensure_visible(current_user, ticket, db)

    if row.status == "accepted":
        if row.assigned_agent_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="webchat handoff already accepted by another agent",
            )
        return core.serialize_handoff_request(
            db,
            row,
            current_user=current_user,
            conversation=conversation,
            ticket=ticket,
        )
    if row.status != "requested":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="webchat handoff request is not open",
        )

    routing.assign_handoff_to_agent(
        db,
        request_row=row,
        conversation=conversation,
        user=current_user,
        mode="manual_accept",
    )
    row = db.get(type(row), int(request_id))
    if row is None:
        raise RuntimeError("handoff_disappeared_after_assignment")
    row.decision_note = core._clip(note, core.MAX_NOTE_CHARS)
    row.updated_at = routing._core.utc_now()
    db.flush()
    conversation = db.get(type(conversation), conversation.id)
    ticket = db.get(type(ticket), ticket.id)
    if conversation is None or ticket is None:
        raise RuntimeError("handoff_context_disappeared_after_assignment")
    return core.serialize_handoff_request(
        db,
        row,
        current_user=current_user,
        conversation=conversation,
        ticket=ticket,
    )


def install_handoff_assignment_contract() -> None:
    """Install one recovery and acceptance authority for Text and Voice Handoffs."""

    global _INSTALLED
    if _INSTALLED:
        return
    from . import agent_routing_service as routing
    from . import webchat_handoff_service_core as core

    routing._eligible_voice_agents = _eligible_voice_agents
    routing._eligible_text_request_for_agent = _eligible_text_request_for_agent
    core.accept_handoff_request = _accept_ticket_handoff
    _INSTALLED = True


__all__ = ["install_handoff_assignment_contract"]
