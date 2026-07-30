from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..models import Ticket, User
from ..models_agent_routing import ConversationControl
from ..utils.time import utc_now
from ..voice_models import WebchatVoiceSession
from ..webchat_models import (
    WebchatConversation,
    WebchatHandoffDecision,
    WebchatHandoffRequest,
)
from .handoff_assignment_state_contract import (
    install_handoff_assignment_state_contract,
)
from .permissions import (
    CAP_WEBCHAT_HANDOFF_ACCEPT,
    ensure_ticket_visible,
    resolve_capabilities,
)

_INSTALLED = False


def _lock(query, db: Session):
    if db.bind and db.bind.dialect.name.startswith("postgresql"):
        return query.with_for_update()
    return query


def _eligible_voice_agents(
    db: Session,
    *,
    request_row,
    control,
):
    """Apply bounded recovery without immediately re-offering Ticketless calls.

    Ticket-backed Handoffs use generation-scoped candidate attempts. Ticketless
    Handoffs have no RoutingPlan, so their existing per-Handoff offer history
    remains the authoritative exclusion set.
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
        if plan is None and routing._core._agent_has_prior_voice_offer(
            db,
            handoff_request_id=request_row.id,
            agent_id=user.id,
        ):
            continue
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
    return bool(
        db.query(WebchatHandoffDecision.id)
        .filter(
            WebchatHandoffDecision.request_id == request_id,
            WebchatHandoffDecision.actor_id == user_id,
            WebchatHandoffDecision.decision == "declined",
        )
        .first()
    )


def _eligible_text_request_for_agent(
    db: Session,
    *,
    user: User,
):
    """Use generation attempts for Ticket-backed routing and Ticketless decline."""

    from . import agent_routing_service as routing

    voice_exists = (
        db.query(WebchatVoiceSession.id)
        .filter(
            WebchatVoiceSession.conversation_id
            == WebchatHandoffRequest.conversation_id,
            WebchatVoiceSession.status.in_(
                sorted(routing._core.VOICE_OPEN_SESSION_STATUSES)
            ),
        )
        .exists()
    )
    query = (
        db.query(
            WebchatHandoffRequest,
            WebchatConversation,
            ConversationControl,
        )
        .join(
            WebchatConversation,
            WebchatConversation.id == WebchatHandoffRequest.conversation_id,
        )
        .join(
            ConversationControl,
            ConversationControl.conversation_id == WebchatConversation.id,
        )
        .filter(
            WebchatHandoffRequest.status == "requested",
            WebchatConversation.status == "open",
            ConversationControl.country_code.is_not(None),
            ~voice_exists,
        )
        .order_by(
            WebchatHandoffRequest.requested_at.asc(),
            WebchatHandoffRequest.id.asc(),
        )
        .limit(100)
    )
    for request_row, conversation, control in _lock(query, db).all():
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
    """Accept a Ticket-backed Handoff through the public routing authority."""

    from . import agent_routing_service as routing
    from . import webchat_handoff_service as handoff

    capabilities = resolve_capabilities(current_user, db)
    if CAP_WEBCHAT_HANDOFF_ACCEPT not in capabilities:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="webchat_handoff_accept_requires_capability",
        )

    row = _lock(
        db.query(WebchatHandoffRequest).filter(
            WebchatHandoffRequest.id == int(request_id)
        ),
        db,
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="webchat handoff request not found",
        )
    conversation = db.get(WebchatConversation, row.conversation_id)
    ticket = db.get(Ticket, row.ticket_id) if row.ticket_id is not None else None
    if conversation is None or ticket is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="webchat handoff source is missing",
        )
    ensure_ticket_visible(current_user, ticket, db)

    if row.status == "accepted":
        if row.assigned_agent_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="webchat handoff already accepted by another agent",
            )
        return handoff.serialize_handoff_request(
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
    row = db.get(WebchatHandoffRequest, int(request_id))
    if row is None:
        raise RuntimeError("handoff_disappeared_after_assignment")
    cleaned_note = " ".join(str(note or "").strip().split())[:1000]
    row.decision_note = cleaned_note or None
    row.updated_at = utc_now()
    db.flush()
    conversation = db.get(WebchatConversation, conversation.id)
    ticket = db.get(Ticket, ticket.id)
    if conversation is None or ticket is None:
        raise RuntimeError("handoff_context_disappeared_after_assignment")
    return handoff.serialize_handoff_request(
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
    from . import webchat_handoff_service as handoff

    install_handoff_assignment_state_contract()
    routing._eligible_voice_agents = _eligible_voice_agents
    routing._eligible_text_request_for_agent = _eligible_text_request_for_agent
    handoff._core.accept_handoff_request = _accept_ticket_handoff
    _INSTALLED = True


__all__ = ["install_handoff_assignment_contract"]
