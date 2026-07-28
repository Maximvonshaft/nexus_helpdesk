from __future__ import annotations

"""Single Scenario-aware agent-routing service authority.

The mechanically retained private primitives module contains only the existing
state-transition implementation used by this facade. Every repository caller
continues to import this module, and every operation that can select, offer,
assign, retry, exhaust, or close a Handoff is implemented explicitly here.
"""

from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .agent_routing_primitives import *  # noqa: F401,F403
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


def _candidate_authorized(
    db: Session,
    *,
    plan,
    control: _core.ConversationControl,
    user: _core.User,
    channel_kind: str,
    exclude_attempted: bool = True,
) -> bool:
    if plan is not None:
        return candidate_is_authorized(
            db,
            plan=plan,
            control=control,
            user=user,
            channel_kind=channel_kind,
            exclude_attempted=exclude_attempted,
        )
    if not user.is_active or not control.country_code:
        return False
    return bool(
        db.query(_core.OperatorQueueScopeGrant.id)
        .filter(
            _core.OperatorQueueScopeGrant.user_id == user.id,
            _core.OperatorQueueScopeGrant.tenant_key == control.tenant_key,
            _core.OperatorQueueScopeGrant.country_code == control.country_code,
            _core.OperatorQueueScopeGrant.channel_key == control.channel_key,
            _core.OperatorQueueScopeGrant.queue_key == "legacy",
            _core.OperatorQueueScopeGrant.enabled.is_(True),
        )
        .first()
    )


def _scope_grant_exists(
    db: Session,
    *,
    user: _core.User,
    control: _core.ConversationControl,
) -> bool:
    request_row = _active_request_for_control(db, control=control)
    plan = (
        ensure_handoff_routing_plan(db, request_row=request_row)
        if request_row is not None
        else None
    )
    return _candidate_authorized(
        db,
        plan=plan,
        control=control,
        user=user,
        channel_kind="text",
        exclude_attempted=False,
    )


def read_agent_state(
    db: Session,
    *,
    user_id: int,
) -> dict[str, Any]:
    expire_voice_offers(db, agent_id=user_id)
    release_expired_voice_wrap_ups(db, user_id=user_id)
    return _core._state_payload(
        db,
        _core.get_or_create_agent_state(db, user_id=user_id),
    )


def set_agent_state(
    db: Session,
    *,
    user: _core.User,
    presence_status: str,
    max_concurrent_conversations: int | None = None,
    voice_enabled: bool | None = None,
    max_concurrent_voice_calls: int | None = None,
    voice_wrap_up_seconds: int | None = None,
) -> dict[str, Any]:
    normalized = str(presence_status or "").strip().lower()
    if normalized not in _core.PRESENCE_STATUSES:
        raise HTTPException(
            status_code=400,
            detail="invalid_agent_presence_status",
        )
    row = _core.get_or_create_agent_state(
        db,
        user_id=user.id,
        lock=True,
    )
    old = _core._state_payload(db, row)
    now = _core.utc_now()
    if max_concurrent_conversations is not None:
        capacity = int(max_concurrent_conversations)
        if not 1 <= capacity <= _core.MAX_AGENT_CAPACITY:
            raise HTTPException(status_code=400, detail="invalid_agent_capacity")
        row.max_concurrent_conversations = capacity
    if voice_enabled is not None:
        if (
            not voice_enabled
            and _core.active_voice_load(db, user_id=user.id) > 0
        ):
            raise HTTPException(
                status_code=409,
                detail="agent_voice_disable_blocked_by_active_call",
            )
        row.voice_enabled = bool(voice_enabled)
    if max_concurrent_voice_calls is not None:
        voice_capacity = int(max_concurrent_voice_calls)
        if not 1 <= voice_capacity <= _core.MAX_VOICE_CAPACITY:
            raise HTTPException(
                status_code=400,
                detail="invalid_agent_voice_capacity",
            )
        if voice_capacity < _core.active_voice_load(db, user_id=user.id):
            raise HTTPException(
                status_code=409,
                detail="agent_voice_capacity_below_active_load",
            )
        row.max_concurrent_voice_calls = voice_capacity
    if voice_wrap_up_seconds is not None:
        wrap_up = int(voice_wrap_up_seconds)
        if not 0 <= wrap_up <= _core.MAX_VOICE_WRAP_UP_SECONDS:
            raise HTTPException(
                status_code=400,
                detail="invalid_agent_voice_wrap_up",
            )
        row.voice_wrap_up_seconds = wrap_up
    if row.status != normalized:
        row.status = normalized
        row.status_changed_at = now
    row.last_heartbeat_at = (
        now if normalized in {"online", "paused"} else None
    )
    row.updated_at = now
    if normalized != "online" or not row.voice_enabled:
        _cancel_agent_voice_offers(
            db,
            agent_id=user.id,
            reason="agent_unavailable",
        )
    db.flush()
    if normalized == "online":
        release_expired_voice_wrap_ups(db, user_id=user.id)
        fill_agent_capacity(db, user=user)
    payload = _core._state_payload(db, row)
    _core.log_admin_audit(
        db,
        actor_id=user.id,
        action="operator_agent_state.updated",
        target_type="operator_agent_state",
        target_id=row.id,
        old_value=old,
        new_value=payload,
    )
    return payload


def heartbeat_agent(
    db: Session,
    *,
    user: _core.User,
) -> dict[str, Any]:
    row = _core.get_or_create_agent_state(
        db,
        user_id=user.id,
        lock=True,
    )
    if row.status == "offline":
        return _core._state_payload(db, row)
    now = _core.utc_now()
    row.last_heartbeat_at = now
    row.updated_at = now
    db.flush()
    expire_voice_offers(db, agent_id=user.id)
    release_expired_voice_wrap_ups(db, user_id=user.id)
    if row.status == "online":
        fill_agent_capacity(db, user=user)
    return _core._state_payload(db, row)


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
    released = _core.release_expired_voice_wrap_ups(
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
    now = _core.utc_now()
    offers = _core._lock(
        db.query(_core.VoiceRoutingOffer).filter(
            _core.VoiceRoutingOffer.agent_id == agent_id,
            _core.VoiceRoutingOffer.status == "offered",
        ),
        db,
    ).all()
    affected_sessions: set[int] = set()
    for offer in offers:
        offer.status = "cancelled"
        offer.cancelled_at = now
        offer.decline_reason = reason[:240]
        offer.updated_at = now
        affected_sessions.add(offer.voice_session_id)
        update_attempt_by_external_ref(
            db,
            external_ref=offer.public_id,
            outcome="cancelled",
            reason_code=reason,
        )
    db.flush()
    for session_id in affected_sessions:
        session = db.get(_core.WebchatVoiceSession, session_id)
        if session is not None:
            create_next_voice_offer(db, voice_session=session)
    return len(offers)


def expire_voice_offers(
    db: Session,
    *,
    agent_id: int | None = None,
    voice_session_id: int | None = None,
    limit: int = 200,
) -> int:
    now = _core.utc_now()
    query = db.query(_core.VoiceRoutingOffer).filter(
        _core.VoiceRoutingOffer.status == "offered",
        _core.VoiceRoutingOffer.expires_at <= now,
    )
    if agent_id is not None:
        query = query.filter(_core.VoiceRoutingOffer.agent_id == agent_id)
    if voice_session_id is not None:
        query = query.filter(
            _core.VoiceRoutingOffer.voice_session_id == voice_session_id
        )
    offers = _core._lock(
        query.order_by(_core.VoiceRoutingOffer.expires_at.asc()).limit(
            max(1, min(limit, 1000))
        ),
        db,
    ).all()
    affected_sessions: set[int] = set()
    for offer in offers:
        offer.status = "expired"
        offer.expired_at = now
        offer.updated_at = now
        affected_sessions.add(offer.voice_session_id)
        update_attempt_by_external_ref(
            db,
            external_ref=offer.public_id,
            outcome="expired",
            reason_code="voice_offer_expired",
        )
        session = db.get(_core.WebchatVoiceSession, offer.voice_session_id)
        conversation = (
            db.get(_core.WebchatConversation, session.conversation_id)
            if session is not None
            else None
        )
        if conversation is not None:
            _core._event(
                db,
                conversation=conversation,
                event_type="voice.offer.expired",
                payload={
                    "voice_session_id": session.public_id,
                    "offer_id": offer.public_id,
                    "agent_id": offer.agent_id,
                },
            )
    db.flush()
    for session_id in affected_sessions:
        session = db.get(_core.WebchatVoiceSession, session_id)
        if session is not None:
            create_next_voice_offer(db, voice_session=session)
    return len(offers)


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
        if not _candidate_authorized(
            db,
            plan=plan,
            control=control,
            user=user,
            channel_kind="voice",
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
    if voice_session.status not in _core.VOICE_OPEN_SESSION_STATUSES:
        return None
    request_row = (
        db.get(_core.WebchatHandoffRequest, voice_session.handoff_request_id)
        if voice_session.handoff_request_id is not None
        else None
    )
    if request_row is None or request_row.status != "requested":
        return None
    plan = ensure_handoff_routing_plan(db, request_row=request_row)
    now = _core.utc_now()
    active_offer = (
        db.query(_core.VoiceRoutingOffer)
        .filter(
            _core.VoiceRoutingOffer.voice_session_id == voice_session.id,
            _core.VoiceRoutingOffer.status == "offered",
            _core.VoiceRoutingOffer.expires_at > now,
        )
        .first()
    )
    if active_offer is not None:
        return active_offer
    conversation = db.get(
        _core.WebchatConversation,
        voice_session.conversation_id,
    )
    if conversation is None or conversation.status != "open":
        return None
    control = _core._control_for_conversation(db, conversation)
    candidates = _eligible_voice_agents(
        db,
        request_row=request_row,
        control=control,
    )
    if not candidates:
        if plan is not None and plan.status == "active":
            schedule_retry_or_exhaust(
                db,
                plan=plan,
                reason_code="no_eligible_voice_candidate",
            )
        return None
    user, _state = candidates[0]
    sequence = int(
        db.query(_core.func.coalesce(_core.func.max(_core.VoiceRoutingOffer.sequence), 0))
        .filter(_core.VoiceRoutingOffer.voice_session_id == voice_session.id)
        .scalar()
        or 0
    ) + 1
    offer = _core.VoiceRoutingOffer(
        public_id=f"vo_{_core.secrets.token_urlsafe(18)}",
        voice_session_id=voice_session.id,
        handoff_request_id=request_row.id,
        agent_id=user.id,
        sequence=sequence,
        status="offered",
        offered_at=now,
        expires_at=now
        + _core.timedelta(
            seconds=_core._voice_offer_timeout_seconds(
                db,
                session=voice_session,
            )
        ),
        created_at=now,
        updated_at=now,
    )
    try:
        with db.begin_nested():
            db.add(offer)
            db.flush()
    except _core.IntegrityError:
        return (
            db.query(_core.VoiceRoutingOffer)
            .filter(
                _core.VoiceRoutingOffer.voice_session_id == voice_session.id,
                _core.VoiceRoutingOffer.status == "offered",
                _core.VoiceRoutingOffer.expires_at > _core.utc_now(),
            )
            .first()
        )
    record_candidate_attempt(
        db,
        plan=plan,
        request_id=request_row.id,
        agent_id=user.id,
        channel_kind="voice",
        outcome="offered",
        external_ref=offer.public_id,
    )
    voice_session.status = "ringing"
    voice_session.ringing_at = voice_session.ringing_at or now
    voice_session.updated_at = now
    _core._event(
        db,
        conversation=conversation,
        event_type="voice.offer.created",
        payload={
            "voice_session_id": voice_session.public_id,
            "handoff_request_id": request_row.id,
            "offer_id": offer.public_id,
            "agent_id": user.id,
            "expires_at": offer.expires_at.isoformat(),
        },
    )
    _core.log_admin_audit(
        db,
        actor_id=None,
        action="voice.offer.created",
        target_type="voice_routing_offer",
        target_id=offer.id,
        new_value={
            "voice_session_id": voice_session.public_id,
            "handoff_request_id": request_row.id,
            "agent_id": user.id,
            "expires_at": offer.expires_at.isoformat(),
        },
    )
    db.flush()
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
    for request_row, conversation, control in _core._lock(query, db).all():
        plan = ensure_handoff_routing_plan(db, request_row=request_row)
        if _candidate_authorized(
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
    if not _candidate_authorized(
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
    result = _core.assign_handoff_to_agent(
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


def accept_voice_offer(
    db: Session,
    *,
    voice_session: _core.WebchatVoiceSession,
    user: _core.User,
) -> dict[str, Any]:
    if voice_session.handoff_request_id is None:
        raise HTTPException(status_code=409, detail="voice_handoff_missing")
    request_row = db.get(
        _core.WebchatHandoffRequest,
        voice_session.handoff_request_id,
    )
    conversation = db.get(
        _core.WebchatConversation,
        voice_session.conversation_id,
    )
    if request_row is None or conversation is None:
        raise HTTPException(status_code=409, detail="voice_handoff_missing")
    offer = (
        db.query(_core.VoiceRoutingOffer)
        .filter(
            _core.VoiceRoutingOffer.voice_session_id == voice_session.id,
            _core.VoiceRoutingOffer.agent_id == user.id,
            _core.VoiceRoutingOffer.status == "offered",
        )
        .first()
    )
    if offer is None:
        raise HTTPException(status_code=409, detail="voice_offer_not_owned")
    return assign_handoff_to_agent(
        db,
        request_row=request_row,
        conversation=conversation,
        user=user,
        mode="voice_offer_accept",
        voice_offer=offer,
    )


def decline_voice_offer(
    db: Session,
    *,
    voice_session: _core.WebchatVoiceSession,
    user: _core.User,
    reason_code: str = "agent_declined_voice_offer",
    note: str | None = None,
) -> dict[str, Any]:
    if voice_session.handoff_request_id is None:
        raise HTTPException(status_code=409, detail="voice_handoff_missing")
    request_row = db.get(
        _core.WebchatHandoffRequest,
        voice_session.handoff_request_id,
    )
    conversation = db.get(
        _core.WebchatConversation,
        voice_session.conversation_id,
    )
    if request_row is None or conversation is None:
        raise HTTPException(status_code=409, detail="voice_handoff_missing")
    now = _core.utc_now()
    offer = _core._lock(
        db.query(_core.VoiceRoutingOffer).filter(
            _core.VoiceRoutingOffer.voice_session_id == voice_session.id,
            _core.VoiceRoutingOffer.agent_id == user.id,
            _core.VoiceRoutingOffer.status == "offered",
        ),
        db,
    ).first()
    if offer is None:
        previous = (
            db.query(_core.VoiceRoutingOffer)
            .filter(
                _core.VoiceRoutingOffer.voice_session_id == voice_session.id,
                _core.VoiceRoutingOffer.agent_id == user.id,
                _core.VoiceRoutingOffer.status == "declined",
            )
            .order_by(_core.VoiceRoutingOffer.id.desc())
            .first()
        )
        if previous is not None:
            return {
                "voice_session_id": voice_session.public_id,
                "offer_id": previous.public_id,
                "status": "declined",
                "idempotent": True,
            }
        raise HTTPException(status_code=409, detail="voice_offer_not_owned")
    offer.status = "declined"
    offer.declined_at = now
    offer.decline_reason = (note or reason_code)[:240]
    offer.updated_at = now
    update_attempt_by_external_ref(
        db,
        external_ref=offer.public_id,
        outcome="declined",
        reason_code=reason_code,
    )
    db.add(
        _core.WebchatHandoffDecision(
            request_id=request_row.id,
            actor_id=user.id,
            decision="declined",
            reason_code=(reason_code or "agent_declined_voice_offer")[:160],
            note=(note or "")[:1000] or None,
            created_at=now,
        )
    )
    voice_session.status = "ringing"
    voice_session.updated_at = now
    conversation.handoff_status = "requested"
    conversation.active_agent_id = None
    conversation.ai_suspended = True
    conversation.ai_suspended_reason = "voice_handoff_waiting"
    conversation.takeover_mode = None
    conversation.updated_at = now
    task = _core._operator_task(db, conversation_id=conversation.id)
    if task is not None:
        task.status = "pending"
        task.assignee_id = None
        task.updated_at = now
    _core._event(
        db,
        conversation=conversation,
        event_type="voice.offer.declined",
        payload={
            "voice_session_id": voice_session.public_id,
            "handoff_request_id": request_row.id,
            "offer_id": offer.public_id,
            "actor_id": user.id,
            "reason_code": reason_code,
        },
    )
    _core.log_admin_audit(
        db,
        actor_id=user.id,
        action="voice.offer.declined",
        target_type="voice_routing_offer",
        target_id=offer.id,
        new_value={
            "voice_session_id": voice_session.public_id,
            "handoff_request_id": request_row.id,
            "reason_code": reason_code,
        },
    )
    db.flush()
    next_offer = create_next_voice_offer(db, voice_session=voice_session)
    return {
        "voice_session_id": voice_session.public_id,
        "offer_id": offer.public_id,
        "status": "declined",
        "next_offer_id": (
            next_offer.public_id if next_offer is not None else None
        ),
    }


def fill_agent_capacity(
    db: Session,
    *,
    user: _core.User,
) -> list[dict[str, Any]]:
    assigned: list[dict[str, Any]] = []
    state = _core.get_or_create_agent_state(
        db,
        user_id=user.id,
        lock=True,
    )
    if state.status != "online" or not _core.heartbeat_is_fresh(state):
        return assigned
    expire_voice_offers(db, agent_id=user.id)
    release_expired_voice_wrap_ups(db, user_id=user.id)
    while (
        _core.active_agent_load(db, user_id=user.id)
        < state.max_concurrent_conversations
    ):
        candidate = _eligible_text_request_for_agent(db, user=user)
        if candidate is None:
            break
        request_row, conversation, _control = candidate
        try:
            assigned.append(
                assign_handoff_to_agent(
                    db,
                    request_row=request_row,
                    conversation=conversation,
                    user=user,
                    mode="automatic",
                )
            )
        except HTTPException as exc:
            if exc.status_code == 409 and exc.detail == "handoff_not_waiting":
                continue
            break
    if state.voice_enabled:
        voice_rows = (
            db.query(_core.WebchatVoiceSession)
            .join(
                _core.WebchatHandoffRequest,
                _core.WebchatHandoffRequest.id
                == _core.WebchatVoiceSession.handoff_request_id,
            )
            .filter(
                _core.WebchatHandoffRequest.status == "requested",
                _core.WebchatVoiceSession.status.in_(["created", "ringing"]),
            )
            .order_by(
                _core.WebchatHandoffRequest.requested_at.asc(),
                _core.WebchatVoiceSession.id.asc(),
            )
            .limit(100)
            .all()
        )
        for voice_session in voice_rows:
            create_next_voice_offer(db, voice_session=voice_session)
    return assigned


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
    existing = _core._lock(
        db.query(_core.WebchatHandoffRequest)
        .filter(
            _core.WebchatHandoffRequest.conversation_id == conversation.id,
            _core.WebchatHandoffRequest.status.in_(["requested", "accepted"]),
        )
        .order_by(_core.WebchatHandoffRequest.id.desc()),
        db,
    ).first()
    now = _core.utc_now()
    if existing is not None:
        if existing.status == "requested":
            existing.reason_code = existing.reason_code or reason_code
            existing.reason_text = existing.reason_text or reason_text
            existing.recommended_agent_action = (
                existing.recommended_agent_action or recommended_agent_action
            )
            existing.trigger_message_id = (
                existing.trigger_message_id or trigger_message_id
            )
            existing.ai_turn_id = existing.ai_turn_id or ai_turn_id
            existing.updated_at = now
            ensure_handoff_routing_plan(db, request_row=existing)
            voice_session = _core._voice_session_for_conversation(
                db,
                conversation_id=conversation.id,
            )
            if voice_session is not None:
                voice_session.handoff_request_id = existing.id
                create_next_voice_offer(db, voice_session=voice_session)
        return existing
    row = _core.WebchatHandoffRequest(
        conversation_id=conversation.id,
        ticket_id=conversation.ticket_id,
        source=(source or "ai_auto")[:40],
        trigger_type=(trigger_type or "handoff_required")[:80],
        status="requested",
        reason_code=(reason_code or "human_review_required")[:160],
        reason_text=(reason_text or "")[:240] or None,
        recommended_agent_action=(recommended_agent_action or "")[:1000]
        or None,
        trigger_message_id=trigger_message_id,
        ai_turn_id=ai_turn_id,
        requested_by_actor_type=(requested_by_actor_type or "system")[:40],
        requested_by_user_id=requested_by_user_id,
        requested_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    plan = ensure_handoff_routing_plan(db, request_row=row)
    voice_session = _core._voice_session_for_conversation(
        db,
        conversation_id=conversation.id,
    )
    if voice_session is not None:
        voice_session.handoff_request_id = row.id
        voice_session.status = "ringing"
        voice_session.ringing_at = voice_session.ringing_at or now
        voice_session.updated_at = now
    conversation.current_handoff_request_id = row.id
    conversation.handoff_status = "requested"
    conversation.active_agent_id = None
    conversation.ai_suspended = True
    conversation.ai_suspended_at = now
    conversation.ai_suspended_by = requested_by_user_id
    conversation.ai_suspended_reason = row.reason_code
    conversation.takeover_mode = None
    conversation.last_handoff_reason = row.reason_code
    conversation.updated_at = now
    _core.cancel_open_ai_turns_for_handoff(
        db,
        conversation=conversation,
        actor_id=requested_by_user_id,
        reason_code="handoff_requested",
    )
    control = _core._control_for_conversation(db, conversation)
    task, _created = _core.create_operator_task(
        db,
        source_type="webchat",
        source_id=str(conversation.id),
        ticket_id=conversation.ticket_id,
        webchat_conversation_id=conversation.id,
        task_type="handoff",
        reason_code=row.reason_code,
        payload={
            "handoff_request_id": row.id,
            "tenant_key": control.tenant_key,
            "country_code": control.country_code,
            "channel_key": control.channel_key,
            "queue_key": plan.owner_queue_key if plan is not None else "legacy",
            "visitor_name": conversation.visitor_name,
            "channel_kind": "voice" if voice_session is not None else "text",
        },
    )
    task.status = "pending"
    task.assignee_id = None
    _core._event(
        db,
        conversation=conversation,
        event_type="handoff.requested",
        payload={
            "handoff_request_id": row.id,
            "source": row.source,
            "trigger_type": row.trigger_type,
            "reason_code": row.reason_code,
            "queue_key": plan.owner_queue_key if plan is not None else "legacy",
            "channel_kind": "voice" if voice_session is not None else "text",
        },
    )
    _core.log_admin_audit(
        db,
        actor_id=requested_by_user_id,
        action="webchat_handoff.requested",
        target_type="webchat_handoff_request",
        target_id=row.id,
        new_value={
            "conversation_id": conversation.id,
            "ticket_id": conversation.ticket_id,
            "reason": row.reason_code,
            "queue_key": plan.owner_queue_key if plan is not None else "legacy",
            "channel_kind": "voice" if voice_session is not None else "text",
        },
    )
    db.flush()
    if voice_session is not None:
        create_next_voice_offer(db, voice_session=voice_session)
    else:
        _auto_assign_text_request(
            db,
            request_row=row,
            conversation=conversation,
            control=control,
        )
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
        if not _candidate_authorized(
            db,
            plan=plan,
            control=control,
            user=user,
            channel_kind="text",
        ):
            continue
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
    control = _core._control_for_conversation(db, conversation)
    if not _scope_grant_exists(db, user=user, control=control):
        raise HTTPException(status_code=403, detail="agent_scope_not_authorized")
    request_id = conversation.current_handoff_request_id
    result = _core.close_conversation(
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
