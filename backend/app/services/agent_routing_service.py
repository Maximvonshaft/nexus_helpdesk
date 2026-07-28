from __future__ import annotations

"""Single Scenario-aware agent-routing service authority.

The prior module is mechanically retained as private routing primitives so this
facade can wire the immutable HandoffRoutingPlan into every live creation,
candidate, offer, assignment, retry, exhaustion, and closure path without
forking any Queue, Handoff, Conversation, or capacity state authority.

All repository callers continue to import this module. The private primitives
module is patched in-process to resolve its internal global calls back through
these governed functions; there is therefore one live routing path, not a
compatibility product or second runtime.
"""

from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from . import agent_routing_primitives as _core
from .handoff_routing_authority import (
    candidate_is_authorized,
    close_routing_plan,
    eligible_agents,
    ensure_handoff_routing_plan,
    mark_plan_assigned,
    record_candidate_attempt,
    schedule_retry_or_exhaust,
    update_attempt_by_external_ref,
)

# Preserve the established public surface. Explicit governed replacements below
# supersede only the routing decisions that must consume HandoffRoutingPlan.
for _export_name in dir(_core):
    if not _export_name.startswith("__"):
        globals().setdefault(_export_name, getattr(_core, _export_name))

_ORIGINAL_RELEASE_EXPIRED_WRAP_UPS = _core.release_expired_voice_wrap_ups
_ORIGINAL_CANCEL_AGENT_VOICE_OFFERS = _core._cancel_agent_voice_offers
_ORIGINAL_EXPIRE_VOICE_OFFERS = _core.expire_voice_offers
_ORIGINAL_CREATE_NEXT_VOICE_OFFER = _core.create_next_voice_offer
_ORIGINAL_ASSIGN_HANDOFF = _core.assign_handoff_to_agent
_ORIGINAL_DECLINE_VOICE_OFFER = _core.decline_voice_offer
_ORIGINAL_REQUEST_HANDOFF = _core.request_handoff
_ORIGINAL_CLOSE_CONVERSATION = _core.close_conversation


def _active_request_for_control(
    db: Session,
    *,
    control: _core.ConversationControl,
) -> _core.WebchatHandoffRequest | None:
    return (
        db.query(_core.WebchatHandoffRequest)
        .filter(
            _core.WebchatHandoffRequest.conversation_id
            == control.conversation_id,
            _core.WebchatHandoffRequest.status.in_(["requested", "accepted"]),
        )
        .order_by(_core.WebchatHandoffRequest.id.desc())
        .first()
    )


def _scope_grant_exists(
    db: Session,
    *,
    user: _core.User,
    control: _core.ConversationControl,
) -> bool:
    if not control.country_code:
        return False
    query = db.query(_core.OperatorQueueScopeGrant.id).filter(
        _core.OperatorQueueScopeGrant.user_id == user.id,
        _core.OperatorQueueScopeGrant.tenant_key == control.tenant_key,
        _core.OperatorQueueScopeGrant.country_code == control.country_code,
        _core.OperatorQueueScopeGrant.channel_key == control.channel_key,
        _core.OperatorQueueScopeGrant.enabled.is_(True),
    )
    request_row = _active_request_for_control(db, control=control)
    if request_row is not None:
        plan = ensure_handoff_routing_plan(db, request_row=request_row)
        if plan is not None:
            query = query.filter(
                _core.OperatorQueueScopeGrant.queue_key
                == plan.owner_queue_key
            )
    return query.first() is not None


def release_expired_voice_wrap_ups(
    db: Session,
    *,
    user_id: int | None = None,
    limit: int = 100,
) -> int:
    now = _core.utc_now()
    query = db.query(_core.WebchatVoiceSession).filter(
        _core.WebchatVoiceSession.wrap_up_expires_at.isnot(None),
        _core.WebchatVoiceSession.wrap_up_expires_at <= now,
    )
    if user_id is not None:
        query = query.join(
            _core.WebchatHandoffRequest,
            _core.WebchatHandoffRequest.id
            == _core.WebchatVoiceSession.handoff_request_id,
        ).filter(
            _core.WebchatHandoffRequest.assigned_agent_id == user_id
        )
    sessions = (
        query.order_by(_core.WebchatVoiceSession.wrap_up_expires_at.asc())
        .limit(max(1, min(int(limit or 100), 500)))
        .all()
    )
    request_ids = {
        int(row.handoff_request_id)
        for row in sessions
        if row.handoff_request_id is not None
    }
    released = _ORIGINAL_RELEASE_EXPIRED_WRAP_UPS(
        db,
        user_id=user_id,
        limit=limit,
    )
    for request_id in request_ids:
        close_routing_plan(
            db,
            request_id=request_id,
            outcome_code="voice_wrap_up_expired",
        )
    return released


def _cancel_agent_voice_offers(
    db: Session,
    *,
    agent_id: int,
    reason: str,
) -> int:
    refs = [
        str(row[0])
        for row in db.query(_core.VoiceRoutingOffer.public_id)
        .filter(
            _core.VoiceRoutingOffer.agent_id == agent_id,
            _core.VoiceRoutingOffer.status == "offered",
        )
        .all()
    ]
    affected = _ORIGINAL_CANCEL_AGENT_VOICE_OFFERS(
        db,
        agent_id=agent_id,
        reason=reason,
    )
    for external_ref in refs:
        update_attempt_by_external_ref(
            db,
            external_ref=external_ref,
            outcome="cancelled",
            reason_code=reason,
        )
    return affected


def expire_voice_offers(
    db: Session,
    *,
    agent_id: int | None = None,
    voice_session_id: int | None = None,
    limit: int = 200,
) -> int:
    now = _core.utc_now()
    query = db.query(_core.VoiceRoutingOffer.public_id).filter(
        _core.VoiceRoutingOffer.status == "offered",
        _core.VoiceRoutingOffer.expires_at <= now,
    )
    if agent_id is not None:
        query = query.filter(_core.VoiceRoutingOffer.agent_id == agent_id)
    if voice_session_id is not None:
        query = query.filter(
            _core.VoiceRoutingOffer.voice_session_id == voice_session_id
        )
    refs = [
        str(row[0])
        for row in query.order_by(_core.VoiceRoutingOffer.expires_at.asc())
        .limit(max(1, min(limit, 1000)))
        .all()
    ]
    affected = _ORIGINAL_EXPIRE_VOICE_OFFERS(
        db,
        agent_id=agent_id,
        voice_session_id=voice_session_id,
        limit=limit,
    )
    for external_ref in refs:
        update_attempt_by_external_ref(
            db,
            external_ref=external_ref,
            outcome="expired",
            reason_code="voice_offer_expired",
        )
    return affected


def _eligible_voice_agents(
    db: Session,
    *,
    request_row: _core.WebchatHandoffRequest,
    control: _core.ConversationControl,
) -> list[tuple[_core.User, _core.OperatorAgentState]]:
    if not control.country_code:
        return []
    plan = ensure_handoff_routing_plan(db, request_row=request_row)
    candidates = eligible_agents(
        db,
        plan=plan,
        control=control,
        channel_kind="voice",
        require_voice=True,
    )
    result: list[tuple[_core.User, _core.OperatorAgentState]] = []
    for user, state in candidates:
        if plan is None and _core._agent_has_prior_voice_offer(
            db,
            handoff_request_id=request_row.id,
            agent_id=user.id,
        ):
            continue
        occupied = _core.active_voice_load(db, user_id=user.id)
        reserved = _core.reserved_voice_offer_count(
            db,
            user_id=user.id,
        )
        if occupied + reserved >= state.max_concurrent_voice_calls:
            continue
        result.append((user, state))
    return result


def create_next_voice_offer(
    db: Session,
    *,
    voice_session: _core.WebchatVoiceSession,
) -> _core.VoiceRoutingOffer | None:
    request_row = (
        db.get(_core.WebchatHandoffRequest, voice_session.handoff_request_id)
        if voice_session.handoff_request_id is not None
        else None
    )
    plan = (
        ensure_handoff_routing_plan(db, request_row=request_row)
        if request_row is not None and request_row.status == "requested"
        else None
    )
    offer = _ORIGINAL_CREATE_NEXT_VOICE_OFFER(
        db,
        voice_session=voice_session,
    )
    if offer is not None and request_row is not None:
        record_candidate_attempt(
            db,
            plan=plan,
            request_id=request_row.id,
            agent_id=offer.agent_id,
            channel_kind="voice",
            outcome="offered",
            external_ref=offer.public_id,
        )
    elif plan is not None and plan.status == "active":
        schedule_retry_or_exhaust(
            db,
            plan=plan,
            reason_code="no_eligible_voice_candidate",
        )
    return offer


def _eligible_text_request_for_agent(
    db: Session,
    *,
    user: _core.User,
) -> tuple[
    _core.WebchatHandoffRequest,
    _core.WebchatConversation,
    _core.ConversationControl,
] | None:
    voice_exists = (
        db.query(_core.WebchatVoiceSession.id)
        .filter(
            _core.WebchatVoiceSession.conversation_id
            == _core.WebchatHandoffRequest.conversation_id,
            _core.WebchatVoiceSession.status.in_(
                sorted(_core.VOICE_OPEN_SESSION_STATUSES)
            ),
        )
        .exists()
    )
    query = (
        db.query(
            _core.WebchatHandoffRequest,
            _core.WebchatConversation,
            _core.ConversationControl,
        )
        .join(
            _core.WebchatConversation,
            _core.WebchatConversation.id
            == _core.WebchatHandoffRequest.conversation_id,
        )
        .join(
            _core.ConversationControl,
            _core.ConversationControl.conversation_id
            == _core.WebchatConversation.id,
        )
        .filter(
            _core.WebchatHandoffRequest.status == "requested",
            _core.WebchatConversation.status == "open",
            _core.ConversationControl.country_code.is_not(None),
            ~voice_exists,
        )
        .order_by(
            _core.WebchatHandoffRequest.requested_at.asc(),
            _core.WebchatHandoffRequest.id.asc(),
        )
        .limit(100)
    )
    rows = _core._lock(query, db).all()
    for request_row, conversation, control in rows:
        plan = ensure_handoff_routing_plan(db, request_row=request_row)
        if plan is None:
            declined = (
                db.query(_core.WebchatHandoffDecision.id)
                .filter(
                    _core.WebchatHandoffDecision.request_id == request_row.id,
                    _core.WebchatHandoffDecision.actor_id == user.id,
                    _core.WebchatHandoffDecision.decision == "declined",
                )
                .first()
            )
            if declined is not None:
                continue
        if candidate_is_authorized(
            db,
            plan=plan,
            control=control,
            user=user,
            channel_kind="text",
        ):
            return request_row, conversation, control
    return None


def assign_handoff_to_agent(
    db: Session,
    *,
    request_row: _core.WebchatHandoffRequest,
    conversation: _core.WebchatConversation,
    user: _core.User,
    mode: str = "automatic",
    voice_offer: _core.VoiceRoutingOffer | None = None,
) -> dict[str, Any]:
    control = _core._control_for_conversation(db, conversation)
    voice_session = _core._voice_session_for_conversation(
        db,
        conversation_id=conversation.id,
    )
    channel_kind = "voice" if voice_session is not None else "text"
    plan = ensure_handoff_routing_plan(db, request_row=request_row)
    if not candidate_is_authorized(
        db,
        plan=plan,
        control=control,
        user=user,
        channel_kind=channel_kind,
        exclude_attempted=False,
    ):
        raise HTTPException(
            status_code=403,
            detail="agent_scenario_scope_not_authorized",
        )
    result = _ORIGINAL_ASSIGN_HANDOFF(
        db,
        request_row=request_row,
        conversation=conversation,
        user=user,
        mode=mode,
        voice_offer=voice_offer,
    )
    record_candidate_attempt(
        db,
        plan=plan,
        request_id=request_row.id,
        agent_id=user.id,
        channel_kind=channel_kind,
        outcome="accepted",
        reason_code=mode,
        external_ref=(voice_offer.public_id if voice_offer is not None else None),
    )
    mark_plan_assigned(db, plan=plan, agent_id=user.id)
    return result


def decline_voice_offer(
    db: Session,
    *,
    voice_session: _core.WebchatVoiceSession,
    user: _core.User,
    reason_code: str = "agent_declined_voice_offer",
    note: str | None = None,
) -> dict[str, Any]:
    offer = (
        db.query(_core.VoiceRoutingOffer)
        .filter(
            _core.VoiceRoutingOffer.voice_session_id == voice_session.id,
            _core.VoiceRoutingOffer.agent_id == user.id,
            _core.VoiceRoutingOffer.status == "offered",
        )
        .first()
    )
    external_ref = offer.public_id if offer is not None else None
    result = _ORIGINAL_DECLINE_VOICE_OFFER(
        db,
        voice_session=voice_session,
        user=user,
        reason_code=reason_code,
        note=note,
    )
    if external_ref is not None:
        update_attempt_by_external_ref(
            db,
            external_ref=external_ref,
            outcome="declined",
            reason_code=reason_code,
        )
    return result


def request_handoff(
    db: Session,
    *,
    conversation: _core.WebchatConversation,
    source: str,
    trigger_type: str,
    reason_code: str | None = None,
    reason_text: str | None = None,
    recommended_agent_action: str | None = None,
    trigger_message_id: int | None = None,
    ai_turn_id: int | None = None,
    requested_by_actor_type: str = "system",
    requested_by_user_id: int | None = None,
) -> _core.WebchatHandoffRequest:
    row = _ORIGINAL_REQUEST_HANDOFF(
        db,
        conversation=conversation,
        source=source,
        trigger_type=trigger_type,
        reason_code=reason_code,
        reason_text=reason_text,
        recommended_agent_action=recommended_agent_action,
        trigger_message_id=trigger_message_id,
        ai_turn_id=ai_turn_id,
        requested_by_actor_type=requested_by_actor_type,
        requested_by_user_id=requested_by_user_id,
    )
    ensure_handoff_routing_plan(db, request_row=row)
    return row


def _auto_assign_text_request(
    db: Session,
    *,
    request_row: _core.WebchatHandoffRequest,
    conversation: _core.WebchatConversation,
    control: _core.ConversationControl,
) -> None:
    plan = ensure_handoff_routing_plan(db, request_row=request_row)
    candidates = eligible_agents(
        db,
        plan=plan,
        control=control,
        channel_kind="text",
    )
    for user, state in candidates:
        if request_row.status != "requested":
            return
        if (
            _core.active_agent_load(db, user_id=user.id)
            >= state.max_concurrent_conversations
        ):
            continue
        assign_handoff_to_agent(
            db,
            request_row=request_row,
            conversation=conversation,
            user=user,
            mode="automatic",
        )
        return
    if plan is not None and plan.status == "active":
        schedule_retry_or_exhaust(
            db,
            plan=plan,
            reason_code="no_eligible_text_candidate",
        )


def close_conversation(
    db: Session,
    *,
    conversation: _core.WebchatConversation,
    user: _core.User,
    outcome: str,
    note: str | None = None,
) -> dict[str, Any]:
    request_id = conversation.current_handoff_request_id
    result = _ORIGINAL_CLOSE_CONVERSATION(
        db,
        conversation=conversation,
        user=user,
        outcome=outcome,
        note=note,
    )
    if request_id is not None:
        close_routing_plan(
            db,
            request_id=int(request_id),
            outcome_code=f"conversation_{outcome}",
        )
    return result


_GOVERNED_OVERRIDES = {
    "_scope_grant_exists": _scope_grant_exists,
    "release_expired_voice_wrap_ups": release_expired_voice_wrap_ups,
    "_cancel_agent_voice_offers": _cancel_agent_voice_offers,
    "expire_voice_offers": expire_voice_offers,
    "_eligible_voice_agents": _eligible_voice_agents,
    "create_next_voice_offer": create_next_voice_offer,
    "_eligible_text_request_for_agent": _eligible_text_request_for_agent,
    "assign_handoff_to_agent": assign_handoff_to_agent,
    "decline_voice_offer": decline_voice_offer,
    "request_handoff": request_handoff,
    "_auto_assign_text_request": _auto_assign_text_request,
    "close_conversation": close_conversation,
}

for _name, _implementation in _GOVERNED_OVERRIDES.items():
    globals()[_name] = _implementation
    setattr(_core, _name, _implementation)

del _export_name, _name, _implementation
